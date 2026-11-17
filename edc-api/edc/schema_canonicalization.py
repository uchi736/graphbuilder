from typing import List
import os
from pathlib import Path
import edc.utils.llm_utils as llm_utils
import re
import unicodedata
import numpy as np
import copy
from tqdm import tqdm
import logging

logger = logging.getLogger(__name__)

# 選択肢の記号は A〜Z の 1 文字で、最後の 1 つを「該当なし」に使う。候補に出せるのは最大 25 個。
MAX_CHOICES = 25


def normalize_name(name) -> str:
    """同名判定に使う照合キー。大文字小文字・全角半角・区切り（_ - 空白）の違いを無視する。

    抽出のプロンプトはリレーション名を小文字・下線区切り（governed_by）で出させる一方、
    語彙は大文字（GOVERNED_BY）で持つため、そのまま比べると同名でも一致しない。
    """
    s = unicodedata.normalize("NFKC", str(name or ""))
    return re.sub(r"[\s_\-]+", "", s).casefold()


def find_same_name(name, vocabulary) -> "str | None":
    """語彙の中から name と同名（normalize_name が一致）の見出し語を返す。完全一致を優先し、無ければ None。"""
    if name in vocabulary:
        return name
    key = normalize_name(name)
    if not key:
        return None
    for entry in vocabulary:
        if normalize_name(entry) == key:
            return entry
    return None


def _env_int(name, default):
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning(f"{name} が整数でないため既定値 {default} を使う")
        return default


def candidate_top_k(vocabulary_size: int) -> int:
    """検証役の LLM に見せる候補の数を決める。

    語彙が CANON_FULL_VOCAB_MAX（既定 25）以下なら語彙全部を見せる。定義文の類似度の上位 5 つに
    絞ると、共通の種類（REFERS_TO・MENTIONS 等）が枠を占め、文書タイプ固有の種類が候補に出ない
    ためである。語彙がそれより大きいときは上位 CANON_TOP_K（既定 15）を見せる。
    選択肢の記号が A〜Z の 1 文字なので、どちらの場合も 25 個を上限とする。
    """
    full_max = _env_int("CANON_FULL_VOCAB_MAX", MAX_CHOICES)
    top_k = _env_int("CANON_TOP_K", 15)
    k = vocabulary_size if vocabulary_size <= full_max else top_k
    return max(1, min(k, MAX_CHOICES, max(vocabulary_size, 1)))


def parse_choice_letter(answer, choice_letters) -> "str | None":
    """検証役の応答から選択肢の記号を 1 つ取り出す。読めなければ None（該当なしと同じ扱い）。

    先頭の 1 文字だけを見ると、候補が 14 個以上あるとき「None of the above」の N を
    14 番目の候補と取り違える。そこで、記号の位置に語がある応答だけを記号とみなさない。
      - 記号の直後に英字が続く（None, The, N/A 等）
      - 文頭の代名詞 I（I think ..., I'm ...）。I が単独、または記号らしい区切り（. ) : 等）や
        大文字の候補名（I GOVERNED_BY）が続く場合は 9 番目の候補として読む
    記号の後に空白と文が続く応答（D is the closest、B governed_by、A⏎⏎because ...）は記号として読む。
    """
    s = unicodedata.normalize("NFKC", str(answer or "")).strip()
    s = re.sub(r"^(?:answer|回答|答え)\s*[:：]\s*", "", s, flags=re.I)
    if re.match(r"^N\s*/\s*A\b", s, flags=re.I):
        return None
    m = re.match(r"^[\s*_`'\"(\[「【]*([A-Z])(?![A-Za-z])", s)
    if not m or m.group(1) not in choice_letters:
        return None
    if m.group(1) == "I" and re.match(r"(?:['’]|[ \t]+[a-z])", s[m.end():]):
        return None
    return m.group(1)


class SchemaCanonicalizer:
    # The class to handle the last stage: Schema Canonicalization
    def __init__(
        self,
        target_schema_dict: dict,
        embedder,
        verify_model=None,
        verify_tokenizer=None,
        verify_openai_model=None,
    ) -> None:
        # The canonicalizer uses an embedding model to first fetch candidates from the target schema, then uses a verifier schema to decide which one to canonicalize to or not
        # canonoicalize at all.

        assert verify_openai_model is not None or (verify_model is not None and verify_tokenizer is not None)
        self.verifier_model = verify_model
        self.verifier_tokenizer = verify_tokenizer
        self.verifier_openai_model = verify_openai_model
        self.schema_dict = target_schema_dict

        self.embedder = embedder

        # Embed the target schema
        self.schema_embedding_dict = {}

        print("Embedding target schema...")
        for relation, relation_definition in tqdm(target_schema_dict.items()):
            embedding = self.embedder.encode(relation_definition)
            self.schema_embedding_dict[relation] = embedding

    def retrieve_similar_relations(self, query_relation_definition: str, top_k=5):
        target_relation_list = list(self.schema_embedding_dict.keys())
        target_relation_embedding_list = list(self.schema_embedding_dict.values())
        if "sts_query" in self.embedder.prompts:
            query_embedding = self.embedder.encode(query_relation_definition, prompt_name="sts_query")
        else:
            query_embedding = self.embedder.encode(query_relation_definition)

        # コサイン類似度（埋め込みを正規化）。生のドット積だと閾値が意味を持たないため。
        q = np.asarray(query_embedding, dtype=float)
        M = np.asarray(target_relation_embedding_list, dtype=float)
        q = q / (np.linalg.norm(q) + 1e-12)
        M = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-12)
        scores = M @ q

        highest_score_indices = np.argsort(-scores)

        return {
            target_relation_list[idx]: self.schema_dict[target_relation_list[idx]]
            for idx in highest_score_indices[:top_k]
        }, [float(scores[idx]) for idx in highest_score_indices[:top_k]]

    def llm_verify(
        self,
        input_text_str: str,
        query_triplet: List[str],
        query_relation_definition: str,
        prompt_template_str: str,
        candidate_relation_definition_dict: dict,
        relation_example_dict: dict = None,
    ):
        canonicalized_triplet = copy.deepcopy(query_triplet)
        choice_letters_list = []
        choices = ""
        candidate_relations = list(candidate_relation_definition_dict.keys())
        candidate_relation_descriptions = list(candidate_relation_definition_dict.values())
        for idx, rel in enumerate(candidate_relations):
            choice_letter = chr(ord("@") + idx + 1)
            choice_letters_list.append(choice_letter)
            choices += f"{choice_letter}. '{rel}': {candidate_relation_descriptions[idx]}\n"
            if relation_example_dict is not None:
                choices += f"Example: '{relation_example_dict[candidate_relations[idx]]['triple']}' can be extracted from '{candidate_relations[idx]['sentence']}'\n"
        choices += f"{chr(ord('@')+idx+2)}. None of the above.\n"

        verification_prompt = prompt_template_str.format_map(
            {
                "input_text": input_text_str,
                "query_triplet": query_triplet,
                "query_relation": query_triplet[1],
                "query_relation_definition": query_relation_definition,
                "choices": choices,
            }
        )

        messages = [{"role": "user", "content": verification_prompt}]
        if self.verifier_openai_model is None:
            # llm_utils.generate_completion_transformers([messages], self.model, self.tokenizer, device=self.device)
            verification_result = llm_utils.generate_completion_transformers(
                messages, self.verifier_model, self.verifier_tokenizer, answer_prepend="Answer: ", max_new_token=5
            )
        else:
            verification_result = llm_utils.openai_chat_completion(
                self.verifier_openai_model, None, messages, max_tokens=512
            )

        letter = parse_choice_letter(verification_result, choice_letters_list)
        if letter is not None:
            canonicalized_triplet[1] = candidate_relations[choice_letters_list.index(letter)]
        else:
            return None

        return canonicalized_triplet

    def canonicalize(
        self,
        input_text_str: str,
        open_triplet,
        open_relation_definition_dict: dict,
        verify_prompt_template: str,
        enrich=False,
    ):

        open_relation = open_triplet[1]

        # 語彙と同名（大文字小文字・区切りの違いだけ）なら、LLM に問わず語彙の見出し語に揃える。
        same_name = find_same_name(open_relation, self.schema_dict)
        if same_name is not None:
            if same_name == open_relation:
                return open_triplet, {}
            canonicalized_triplet = copy.deepcopy(open_triplet)
            canonicalized_triplet[1] = same_name
            return canonicalized_triplet, {}

        candidate_relations = []
        candidate_scores = []

        if len(self.schema_dict) != 0:
            if open_relation not in open_relation_definition_dict:
                canonicalized_triplet = None
            else:
                candidate_relations, candidate_scores = self.retrieve_similar_relations(
                    open_relation_definition_dict[open_relation],
                    top_k=candidate_top_k(len(self.schema_dict)),
                )
                canonicalized_triplet = self.llm_verify(
                    input_text_str,
                    open_triplet,
                    open_relation_definition_dict[open_relation],
                    verify_prompt_template,
                    candidate_relations,
                    None,
                )
        else:
            canonicalized_triplet = None

        if canonicalized_triplet is None:
            # 類似度マージゲート: LLMが「該当なし」でも、最近傍の既存関係とのコサイン類似度が
            # 閾値τ(CANON_MERGE_THRESHOLD)以上なら、新規追加せず最近傍へ統合する。
            # これにより enrich によるスキーマの際限ない増殖を防ぎ、本当に新しい関係だけ追加される。
            merge_threshold = llm_utils.merge_threshold()  # 既定は無効。LLM の「該当なし」を類似度で覆さない
            if merge_threshold is not None and candidate_relations and candidate_scores and candidate_scores[0] >= merge_threshold:
                nearest_relation = next(iter(candidate_relations))
                canonicalized_triplet = copy.deepcopy(open_triplet)
                canonicalized_triplet[1] = nearest_relation
            elif enrich:
                definition = open_relation_definition_dict.get(open_relation, open_relation)
                self.schema_dict[open_relation] = definition
                if "sts_query" in self.embedder.prompts:
                    embedding = self.embedder.encode(
                        definition, prompt_name="sts_query"
                    )
                else:
                    embedding = self.embedder.encode(definition)
                self.schema_embedding_dict[open_relation] = embedding
                canonicalized_triplet = open_triplet
        return canonicalized_triplet, dict(zip(candidate_relations, candidate_scores))
