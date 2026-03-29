from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from .config import get_settings
from .logging_setup import logger
from .prompts import (
    JOB_SUMMARY_PROMPT,
    MATCH_SCORE_PROMPT,
    PLAN_PROMPT,
    SCREENING_ANSWER_PROMPT,
    GENERATE_SEARCHES_PROMPT,
    EXPLORE_QUERIES_PROMPT,
    SUGGESTION_ENGINE_PROMPT,
    FORM_FIELD_ANSWER_PROMPT,
)


def _extract_json(text: str) -> str:
    """Extract JSON from text, stripping markdown code fences if present."""
    text = text.strip()
    fence_pattern = r"^```(?:json)?\s*\n?(.*?)\n?```$"
    match = re.match(fence_pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _parse_match_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Standardize match response format."""
    return {
        "match_score": int(raw.get("match_score", 50)),
        "top_reasons": list(raw.get("top_reasons", [])),
        "missing_requirements": list(raw.get("missing_requirements", [])),
        "inferred_qualifications": list(raw.get("inferred_qualifications", [])),
        "suggested_resume_bullets": list(raw.get("suggested_resume_bullets", [])),
    }


def _fallback_match_response() -> Dict[str, Any]:
    """Return fallback response when LLM fails."""
    return {
        "match_score": 50,
        "top_reasons": ["Fallback score due to LLM failure."],
        "missing_requirements": [],
        "inferred_qualifications": [],
        "suggested_resume_bullets": [],
    }


class BaseLLM(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    def summarize_job(self, job_description: str) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def score_match(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def generate_screening_answer(self, profile_json: str, resume_text: str, question: str) -> str:
        pass
    
    @abstractmethod
    def plan_steps(self, task: str) -> List[str]:
        pass
    
    @abstractmethod
    def generate_search_queries(self, resume_text: str) -> List[Dict[str, Any]]:
        pass
    
    @abstractmethod
    def generate_exploration_queries(
        self,
        resume_text: str,
        search_history_context: str = "",
        successful_terms: str = "",
        job_context: str = "",
    ) -> List[Dict[str, Any]]:
        pass
    
    @abstractmethod
    def generate_form_field_answer(
        self,
        profile_json: str,
        resume_text: str,
        field_label: str,
        field_type: str,
        required: bool,
        options: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate an answer for a form field based on profile and resume."""
        pass

    @abstractmethod
    def generate_suggestion_engine_queries(
        self,
        *,
        resume_text: str,
        applied_titles: str,
        search_history_context: str,
        successful_terms: str,
        web_context: str,
        random_seed: int,
        banned_queries: str,
        suggestion_count: int,
    ) -> List[Dict[str, Any]]:
        pass
    
    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass


class OllamaLLM(BaseLLM):
    """Local Ollama LLM provider."""

    def __init__(self) -> None:
        s = get_settings()
        self._model = ChatOllama(
            model=s.env.ollama_model,
            base_url=s.env.ollama_base_url,
            temperature=0.2,
            num_ctx=8192,
        )
        self._model_name = s.env.ollama_model
        logger.info("Initialized OllamaLLM with model: {}", self._model_name)

    @property
    def provider_name(self) -> str:
        return f"Ollama ({self._model_name})"

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3))
    def _invoke_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        logger.debug("Invoking Ollama with system prompt: {}", system_prompt[:100])
        resp = self._model.invoke(messages)
        text = resp.content if hasattr(resp, "content") else str(resp)
        text = _extract_json(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("Malformed JSON from Ollama: {}", text)
            raise

    def summarize_job(self, job_description: str) -> Dict[str, Any]:
        payload = JOB_SUMMARY_PROMPT.format(job_description=job_description)
        try:
            return self._invoke_json("You summarize job descriptions.", payload)
        except RetryError:
            return {"summary_markdown": job_description[:500] + "...", "key_skills": []}

    def score_match(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        payload = MATCH_SCORE_PROMPT.format(
            resume_text=resume_text,
            job_description=job_description,
        )
        try:
            raw = self._invoke_json("You score job matches.", payload)
            return _parse_match_response(raw)
        except RetryError:
            return _fallback_match_response()

    def generate_screening_answer(
        self, profile_json: str, resume_text: str, question: str
    ) -> str:
        payload = SCREENING_ANSWER_PROMPT.format(
            profile_json=profile_json,
            resume_text=resume_text,
            question=question,
        )
        try:
            raw = self._invoke_json("You draft concise screening answers.", payload)
            return str(raw.get("answer", "")).strip()
        except RetryError:
            return ""

    def plan_steps(self, task: str) -> List[str]:
        payload = PLAN_PROMPT.format(task=task)
        try:
            raw = self._invoke_json("You plan safe browser automation steps.", payload)
            steps = raw.get("steps") or []
            return [str(s) for s in steps]
        except RetryError:
            return [task]

    def generate_search_queries(self, resume_text: str) -> List[Dict[str, Any]]:
        payload = GENERATE_SEARCHES_PROMPT.format(resume_text=resume_text)
        try:
            raw = self._invoke_json(
                "You generate optimized job search queries based on a resume.",
                payload
            )
            searches = raw.get("searches") or []
            return [
                {
                    "query": str(s.get("query", "")),
                    "category": str(s.get("category", "role")),
                    "priority": int(s.get("priority", 2)),
                }
                for s in searches
                if s.get("query")
            ]
        except RetryError:
            return []

    def generate_exploration_queries(
        self,
        resume_text: str,
        search_history_context: str = "",
        successful_terms: str = "",
        job_context: str = "",
    ) -> List[Dict[str, Any]]:
        payload = EXPLORE_QUERIES_PROMPT.format(
            resume_text=resume_text,
            search_history_context=search_history_context or "No search history available yet.",
            successful_terms=successful_terms or "No data available yet.",
            job_context=job_context or "No high-match jobs analyzed yet.",
        )
        try:
            raw = self._invoke_json(
                "You generate exploration search queries based on past search effectiveness.",
                payload
            )
            searches = raw.get("searches") or []
            return [
                {
                    "query": str(s.get("query", "")),
                    "category": str(s.get("category", "exploratory")),
                    "priority": int(s.get("priority", 2)),
                    "rationale": str(s.get("rationale", "")),
                }
                for s in searches
                if s.get("query")
            ]
        except RetryError:
            logger.error("Failed to generate exploration queries via Ollama")
            return []

    def generate_form_field_answer(
        self,
        profile_json: str,
        resume_text: str,
        field_label: str,
        field_type: str,
        required: bool,
        options: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload = FORM_FIELD_ANSWER_PROMPT.format(
            profile_json=profile_json,
            resume_text=resume_text[:2000],  # Truncate for efficiency
            field_label=field_label,
            field_type=field_type,
            required="Yes" if required else "No",
            options=", ".join(options) if options else "N/A",
        )
        try:
            raw = self._invoke_json(
                "You generate concise answers for job application form fields.",
                payload
            )
            return {
                "answer": str(raw.get("answer", "")).strip(),
                "confidence": str(raw.get("confidence", "medium")),
            }
        except RetryError:
            logger.error("Failed to generate form field answer via Ollama")
            return {"answer": "", "confidence": "low"}

    def generate_suggestion_engine_queries(
        self,
        *,
        resume_text: str,
        applied_titles: str,
        search_history_context: str,
        successful_terms: str,
        web_context: str,
        random_seed: int,
        banned_queries: str,
        suggestion_count: int,
    ) -> List[Dict[str, Any]]:
        payload = SUGGESTION_ENGINE_PROMPT.format(
            resume_text=resume_text,
            applied_titles=applied_titles or "No applied job data yet.",
            search_history_context=search_history_context or "No search history yet.",
            successful_terms=successful_terms or "No successful terms yet.",
            web_context=web_context or "No web context available.",
            random_seed=random_seed,
            banned_queries=banned_queries or "None",
            suggestion_count=suggestion_count,
        )
        try:
            raw = self._invoke_json(
                "You generate diverse job search suggestions. Respond with valid JSON only.",
                payload,
            )
            searches = raw.get("searches") or []
            return [
                {
                    "query": str(s.get("query", "")),
                    "category": str(s.get("category", "role")),
                    "priority": int(s.get("priority", 2)),
                    "rationale": str(s.get("rationale", "")),
                }
                for s in searches
                if s.get("query")
            ]
        except RetryError:
            logger.error("Failed to generate suggestion engine queries via Ollama")
            return []


class OpenAILLM(BaseLLM):
    """OpenAI cloud LLM provider."""

    def __init__(self) -> None:
        s = get_settings()
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")
        
        api_key = s.env.openai_api_key
        if not api_key:
            raise ValueError("OPENAI_API_KEY not set. Add it to your .env file.")
        
        self._client = OpenAI(api_key=api_key)
        self._model = s.env.openai_model
        logger.info("Initialized OpenAILLM with model: {}", self._model)

    @property
    def provider_name(self) -> str:
        return f"OpenAI ({self._model})"

    @retry(wait=wait_exponential(multiplier=1, min=1, max=8), stop=stop_after_attempt(3))
    def _invoke_json(self, system_prompt: str, user_prompt: str) -> Dict[str, Any]:
        logger.debug("Invoking OpenAI with model: {}", self._model)
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            logger.error("OpenAI API error: {}", str(e))
            raise
        
        text = response.choices[0].message.content or "{}"
        logger.debug("OpenAI raw response (first 500 chars): {}", text[:500])
        text = _extract_json(text)
        try:
            result = json.loads(text)
            logger.debug("OpenAI parsed result keys: {}", list(result.keys()))
            return result
        except json.JSONDecodeError:
            logger.error("Malformed JSON from OpenAI: {}", text)
            raise

    def summarize_job(self, job_description: str) -> Dict[str, Any]:
        payload = JOB_SUMMARY_PROMPT.format(job_description=job_description)
        try:
            return self._invoke_json("You summarize job descriptions. Respond with valid JSON.", payload)
        except RetryError:
            return {"summary_markdown": job_description[:500] + "...", "key_skills": []}

    def score_match(self, resume_text: str, job_description: str) -> Dict[str, Any]:
        payload = MATCH_SCORE_PROMPT.format(
            resume_text=resume_text,
            job_description=job_description,
        )
        logger.debug("OpenAI score_match - resume length: {}, job desc length: {}", 
                     len(resume_text), len(job_description))
        try:
            raw = self._invoke_json(
                "You are a job matching expert. Analyze the CV and job description, then respond with valid JSON.",
                payload
            )
            logger.debug("OpenAI raw match result: score={}, inferred={}", 
                        raw.get("match_score"), len(raw.get("inferred_qualifications", [])))
            return _parse_match_response(raw)
        except RetryError as e:
            logger.error("OpenAI score_match failed after retries: {}", str(e))
            return _fallback_match_response()
        except Exception as e:
            logger.error("OpenAI score_match unexpected error: {}", str(e))
            return _fallback_match_response()

    def generate_screening_answer(
        self, profile_json: str, resume_text: str, question: str
    ) -> str:
        payload = SCREENING_ANSWER_PROMPT.format(
            profile_json=profile_json,
            resume_text=resume_text,
            question=question,
        )
        try:
            raw = self._invoke_json("You draft concise screening answers. Respond with valid JSON.", payload)
            return str(raw.get("answer", "")).strip()
        except RetryError:
            return ""

    def plan_steps(self, task: str) -> List[str]:
        payload = PLAN_PROMPT.format(task=task)
        try:
            raw = self._invoke_json("You plan safe browser automation steps. Respond with valid JSON.", payload)
            steps = raw.get("steps") or []
            return [str(s) for s in steps]
        except RetryError:
            return [task]

    def generate_search_queries(self, resume_text: str) -> List[Dict[str, Any]]:
        payload = GENERATE_SEARCHES_PROMPT.format(resume_text=resume_text)
        try:
            raw = self._invoke_json(
                "You generate optimized job search queries based on a resume. Respond with valid JSON.",
                payload
            )
            searches = raw.get("searches") or []
            return [
                {
                    "query": str(s.get("query", "")),
                    "category": str(s.get("category", "role")),
                    "priority": int(s.get("priority", 2)),
                }
                for s in searches
                if s.get("query")
            ]
        except RetryError:
            return []

    def generate_exploration_queries(
        self,
        resume_text: str,
        search_history_context: str = "",
        successful_terms: str = "",
        job_context: str = "",
    ) -> List[Dict[str, Any]]:
        payload = EXPLORE_QUERIES_PROMPT.format(
            resume_text=resume_text,
            search_history_context=search_history_context or "No search history available yet.",
            successful_terms=successful_terms or "No data available yet.",
            job_context=job_context or "No high-match jobs analyzed yet.",
        )
        try:
            raw = self._invoke_json(
                "You generate exploration search queries based on past search effectiveness. Respond with valid JSON.",
                payload
            )
            searches = raw.get("searches") or []
            return [
                {
                    "query": str(s.get("query", "")),
                    "category": str(s.get("category", "exploratory")),
                    "priority": int(s.get("priority", 2)),
                    "rationale": str(s.get("rationale", "")),
                }
                for s in searches
                if s.get("query")
            ]
        except RetryError:
            logger.error("Failed to generate exploration queries via OpenAI")
            return []

    def generate_form_field_answer(
        self,
        profile_json: str,
        resume_text: str,
        field_label: str,
        field_type: str,
        required: bool,
        options: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload = FORM_FIELD_ANSWER_PROMPT.format(
            profile_json=profile_json,
            resume_text=resume_text[:2000],  # Truncate for efficiency
            field_label=field_label,
            field_type=field_type,
            required="Yes" if required else "No",
            options=", ".join(options) if options else "N/A",
        )
        try:
            raw = self._invoke_json(
                "You generate concise answers for job application form fields. Respond with valid JSON.",
                payload
            )
            return {
                "answer": str(raw.get("answer", "")).strip(),
                "confidence": str(raw.get("confidence", "medium")),
            }
        except RetryError:
            logger.error("Failed to generate form field answer via OpenAI")
            return {"answer": "", "confidence": "low"}

    def generate_suggestion_engine_queries(
        self,
        *,
        resume_text: str,
        applied_titles: str,
        search_history_context: str,
        successful_terms: str,
        web_context: str,
        random_seed: int,
        banned_queries: str,
        suggestion_count: int,
    ) -> List[Dict[str, Any]]:
        payload = SUGGESTION_ENGINE_PROMPT.format(
            resume_text=resume_text,
            applied_titles=applied_titles or "No applied job data yet.",
            search_history_context=search_history_context or "No search history yet.",
            successful_terms=successful_terms or "No successful terms yet.",
            web_context=web_context or "No web context available.",
            random_seed=random_seed,
            banned_queries=banned_queries or "None",
            suggestion_count=suggestion_count,
        )
        try:
            raw = self._invoke_json(
                "You generate diverse job search suggestions. Respond with valid JSON only.",
                payload,
            )
            searches = raw.get("searches") or []
            return [
                {
                    "query": str(s.get("query", "")),
                    "category": str(s.get("category", "role")),
                    "priority": int(s.get("priority", 2)),
                    "rationale": str(s.get("rationale", "")),
                }
                for s in searches
                if s.get("query")
            ]
        except RetryError:
            logger.error("Failed to generate suggestion engine queries via OpenAI")
            return []


# Type alias for any LLM provider
LLMClient = Union[OllamaLLM, OpenAILLM]

_client: Optional[BaseLLM] = None


def get_llm() -> BaseLLM:
    """Get the configured LLM provider (singleton)."""
    global _client
    if _client is None:
        s = get_settings()
        provider = s.env.llm_provider.lower()
        
        if provider == "openai":
            _client = OpenAILLM()
        else:
            _client = OllamaLLM()
        
        logger.info("Using LLM provider: {}", _client.provider_name)
    
    return _client


def get_llm_provider_name() -> str:
    """Get the name of the current LLM provider for display."""
    return get_llm().provider_name

