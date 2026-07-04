"""문서 청크 + 검색 (이중 백엔드).

chromadb + sentence-transformers 가 설치돼 있으면 임베딩 벡터 검색,
없으면 의존성 0의 내장 BM25 키워드 검색으로 자동 강등한다.

두 백엔드 공통 인터페이스:
    build_index(reset: bool) -> int      # 색인한 청크 수
    search(query, top_k) -> list[(text, source, score)]

한국어 토큰화는 공백 토큰 + 2-gram 혼합(간단하지만 실용).
data/laws/*.md 는 YAML frontmatter(source_law, articles, verified_date, url)를
메타데이터로 파싱하고 본문만 청크한다.
"""
from __future__ import annotations

import glob
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# 문서 로딩 / frontmatter 파싱 / 청킹
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    text: str
    source: str          # 사람이 읽는 출처 라벨 (source_law + 파일명)
    file: str            # 파일 경로(파일명)
    meta: dict


_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """아주 단순한 YAML frontmatter 파서(의존성 회피용).

    key: value 및 리스트(- item, 또는 [a, b]) 정도만 지원. 실패해도 본문은 보존.
    """
    m = _FM_RE.match(raw)
    if not m:
        return {}, raw
    block = m.group(1)
    body = raw[m.end():]
    meta: dict = {}
    cur_key = None
    for line in block.splitlines():
        if not line.strip():
            continue
        # 리스트 아이템 (들여쓰기 후 "- ")
        if re.match(r"^\s*-\s+", line) and cur_key:
            val = re.sub(r"^\s*-\s+", "", line).strip().strip("'\"")
            meta.setdefault(cur_key, [])
            if isinstance(meta[cur_key], list):
                meta[cur_key].append(val)
            continue
        mkv = re.match(r"^([A-Za-z0-9_]+)\s*:\s*(.*)$", line)
        if mkv:
            key, val = mkv.group(1), mkv.group(2).strip()
            cur_key = key
            if val == "":
                meta[key] = []  # 이어지는 리스트 예상
            elif val.startswith("[") and val.endswith("]"):
                items = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
                meta[key] = items
            else:
                meta[key] = val.strip("'\"")
    return meta, body


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if chunk_size <= 0:
        return [text]
    # 문단 경계를 존중하되 chunk_size 문자 근처에서 자른다.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 1 <= chunk_size:
            buf = (buf + "\n" + p).strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= chunk_size:
                buf = p
            else:
                # 긴 문단은 슬라이딩 윈도우로 분해
                start = 0
                while start < len(p):
                    end = start + chunk_size
                    chunks.append(p[start:end])
                    start = end - overlap if end - overlap > start else end
                buf = ""
    if buf:
        chunks.append(buf)

    # overlap 적용(문단 조립 결과에 대해 앞 청크 꼬리를 이어붙임)
    if overlap > 0 and len(chunks) > 1:
        out = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap:]
            out.append((tail + "\n" + chunks[i]).strip())
        chunks = out
    return chunks


def load_chunks(laws_dir: str, chunk_size: int, overlap: int) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not os.path.isdir(laws_dir):
        return chunks
    files = sorted(glob.glob(os.path.join(laws_dir, "*.md")))
    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            continue
        meta, body = parse_frontmatter(raw)
        fname = os.path.basename(path)
        source_law = meta.get("source_law") or meta.get("source") or fname
        for piece in chunk_text(body, chunk_size, overlap):
            src_label = source_law
            arts = meta.get("articles")
            if arts:
                arts_str = ", ".join(arts) if isinstance(arts, list) else str(arts)
                src_label = f"{source_law} ({arts_str})"
            chunks.append(
                Chunk(text=piece, source=src_label, file=fname, meta=meta)
            )
    return chunks


# ---------------------------------------------------------------------------
# 토큰화 (한국어 대응: 공백 토큰 + 2-gram 혼합)
# ---------------------------------------------------------------------------
_TOK_RE = re.compile(r"[0-9A-Za-z가-힣]+")


def tokenize(text: str) -> list[str]:
    text = (text or "").lower()
    words = _TOK_RE.findall(text)
    toks: list[str] = []
    for w in words:
        toks.append(w)
        # 한글/긴 토큰은 2-gram 도 추가 (형태소 분석기 없이 부분 매칭 강화)
        if len(w) >= 2:
            for i in range(len(w) - 1):
                toks.append(w[i:i + 2])
    return toks


# ---------------------------------------------------------------------------
# 백엔드 1: BM25 (의존성 0, 항상 사용 가능)
# ---------------------------------------------------------------------------
class BM25Backend:
    name = "bm25"

    def __init__(self, index_dir: str, chunk_size: int, overlap: int):
        self.index_dir = index_dir
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.index_path = os.path.join(index_dir, "bm25_index.json")
        self.k1 = 1.5
        self.b = 0.75
        self._docs: list[dict] = []      # {text, source, file, tokens(list)}
        self._df: Counter = Counter()
        self._avgdl = 0.0
        self._loaded = False

    def build_index(self, chunks: list[Chunk], reset: bool) -> int:
        os.makedirs(self.index_dir, exist_ok=True)
        if reset and os.path.exists(self.index_path):
            os.remove(self.index_path)
        docs = []
        df: Counter = Counter()
        total_len = 0
        for c in chunks:
            toks = tokenize(c.text)
            tf = Counter(toks)
            for term in tf:
                df[term] += 1
            total_len += len(toks)
            docs.append(
                {"text": c.text, "source": c.source, "file": c.file, "tf": dict(tf), "len": len(toks)}
            )
        avgdl = (total_len / len(docs)) if docs else 0.0
        payload = {
            "backend": "bm25",
            "docs": docs,
            "df": dict(df),
            "avgdl": avgdl,
            "N": len(docs),
        }
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        self._docs = docs
        self._df = df
        self._avgdl = avgdl
        self._loaded = True
        return len(docs)

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return True
        if not os.path.exists(self.index_path):
            return False
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        self._docs = payload.get("docs", [])
        self._df = Counter(payload.get("df", {}))
        self._avgdl = payload.get("avgdl", 0.0)
        self._loaded = True
        return True

    def search(self, query: str, top_k: int) -> list[tuple[str, str, float]]:
        if not self._ensure_loaded() or not self._docs:
            return []
        q_terms = tokenize(query)
        N = len(self._docs)
        scores: list[tuple[float, int]] = []
        for i, doc in enumerate(self._docs):
            tf = doc["tf"]
            dl = doc["len"] or 1
            s = 0.0
            for term in q_terms:
                if term not in tf:
                    continue
                df = self._df.get(term, 0)
                if df == 0:
                    continue
                idf = math.log(1 + (N - df + 0.5) / (df + 0.5))
                f = tf[term]
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
                s += idf * (f * (self.k1 + 1)) / (denom or 1)
            if s > 0:
                scores.append((s, i))
        scores.sort(reverse=True)
        out = []
        for s, i in scores[:top_k]:
            d = self._docs[i]
            out.append((d["text"], d["source"], round(s, 4)))
        return out


# ---------------------------------------------------------------------------
# 백엔드 2: Chroma + sentence-transformers (있으면 사용)
# ---------------------------------------------------------------------------
class ChromaBackend:
    name = "chroma"

    def __init__(self, index_dir: str, collection_name: str, embed_model: str,
                 chunk_size: int, overlap: int):
        self.index_dir = index_dir
        self.collection_name = collection_name
        self.embed_model = embed_model
        self.chunk_size = chunk_size
        self.overlap = overlap
        self._client = None
        self._embedder = None

    def _get_client(self):
        if self._client is None:
            import chromadb  # 지연 import
            os.makedirs(self.index_dir, exist_ok=True)
            self._client = chromadb.PersistentClient(path=self.index_dir)
        return self._client

    def _get_embedder(self):
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer  # 지연 import
            self._embedder = SentenceTransformer(self.embed_model)
        return self._embedder

    def _embed(self, texts: list[str]) -> list[list[float]]:
        emb = self._get_embedder()
        vecs = emb.encode(texts, convert_to_numpy=True, normalize_embeddings=True)
        return [v.tolist() for v in vecs]

    def build_index(self, chunks: list[Chunk], reset: bool) -> int:
        client = self._get_client()
        if reset:
            try:
                client.delete_collection(self.collection_name)
            except Exception:
                pass
        coll = client.get_or_create_collection(
            name=self.collection_name, metadata={"hnsw:space": "cosine"}
        )
        if not chunks:
            return 0
        texts = [c.text for c in chunks]
        embeds = self._embed(texts)
        ids = [f"chunk-{i}" for i in range(len(chunks))]
        metadatas = [{"source": c.source, "file": c.file} for c in chunks]
        coll.add(ids=ids, documents=texts, embeddings=embeds, metadatas=metadatas)
        return len(chunks)

    def search(self, query: str, top_k: int) -> list[tuple[str, str, float]]:
        client = self._get_client()
        try:
            coll = client.get_collection(self.collection_name)
        except Exception:
            return []
        qv = self._embed([query])[0]
        res = coll.query(query_embeddings=[qv], n_results=top_k)
        out = []
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, text in enumerate(docs):
            src = (metas[i] or {}).get("source", "") if i < len(metas) else ""
            dist = dists[i] if i < len(dists) else 0.0
            score = round(1.0 - float(dist), 4)  # cosine distance → 유사도
            out.append((text, src, score))
        return out


# ---------------------------------------------------------------------------
# 공개 파사드: 백엔드 자동 선택
# ---------------------------------------------------------------------------
class VectorStore:
    def __init__(self, config):
        self.config = config
        self.index_dir = config.vector_store_path
        self.chunk_size = config.retrieval.chunk_size
        self.overlap = config.retrieval.chunk_overlap
        self.laws_dir = config.laws_dir
        self.backend = self._select_backend()

    def _select_backend(self):
        try:
            import chromadb  # noqa: F401
            import sentence_transformers  # noqa: F401
            return ChromaBackend(
                index_dir=self.index_dir,
                collection_name=self.config.vector_store.collection_name,
                embed_model=self.config.embedding.model,
                chunk_size=self.chunk_size,
                overlap=self.overlap,
            )
        except Exception:
            return BM25Backend(
                index_dir=self.index_dir,
                chunk_size=self.chunk_size,
                overlap=self.overlap,
            )

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "unknown")

    def build_index(self, reset: bool = True) -> int:
        chunks = load_chunks(self.laws_dir, self.chunk_size, self.overlap)
        try:
            return self.backend.build_index(chunks, reset=reset)
        except Exception:
            # chroma 가 import 는 됐지만 런타임 실패 시 BM25 로 강등
            if not isinstance(self.backend, BM25Backend):
                self.backend = BM25Backend(self.index_dir, self.chunk_size, self.overlap)
                return self.backend.build_index(chunks, reset=reset)
            raise

    def search(self, query: str, top_k: int | None = None) -> list[tuple[str, str, float]]:
        k = top_k or self.config.retrieval.top_k
        try:
            return self.backend.search(query, k)
        except Exception:
            if not isinstance(self.backend, BM25Backend):
                self.backend = BM25Backend(self.index_dir, self.chunk_size, self.overlap)
                return self.backend.search(query, k)
            return []
