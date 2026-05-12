import json
import re
from dataclasses import dataclass

import anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class GradeResult:
    score: float
    max_score: float
    percentage: float
    feedback: str
    question_type: str


class Grader:
    """Grades student submissions using Claude with prompt caching.

    The assignment context (question + rubric) is cached so all submissions
    for the same activity share a single cache hit, cutting costs and latency.
    """

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model

    # ── Public API ────────────────────────────────────────────────────────────

    def grade(
        self,
        question: str,
        student_answer: str,
        max_points: float,
        model_answer: str = "",
        rubric: str = "",
        question_type: str = "dissertativa",
    ) -> GradeResult:
        if not student_answer.strip():
            return GradeResult(
                score=0.0,
                max_score=max_points,
                percentage=0.0,
                feedback="Sem resposta enviada.",
                question_type=question_type,
            )

        if question_type == "multipla_escolha":
            return self._grade_mc(question, student_answer, max_points, model_answer)
        return self._grade_dissertativa(
            question, student_answer, max_points, model_answer, rubric
        )

    # ── Multiple choice ───────────────────────────────────────────────────────

    def _grade_mc(
        self, question: str, answer: str, max_points: float, correct: str
    ) -> GradeResult:
        context = self._mc_context(question, correct, max_points)
        student_part = f"Resposta do aluno: {answer}\n\nRetorne o JSON."

        raw = self._call(context, student_part, max_tokens=512)
        data = self._parse_json(raw)

        is_correct = bool(data.get("correct", False))
        score = max_points if is_correct else 0.0
        return GradeResult(
            score=score,
            max_score=max_points,
            percentage=100.0 if is_correct else 0.0,
            feedback=data.get("feedback", ""),
            question_type="multipla_escolha",
        )

    def _mc_context(self, question: str, correct: str, max_points: float) -> str:
        return (
            f"Questão de múltipla escolha:\n"
            f"Enunciado: {question}\n"
            f"Resposta correta: {correct}\n"
            f"Pontuação máxima: {max_points}\n\n"
            f'Formato de resposta obrigatório: {{"correct": true/false, "feedback": "..."}}'
        )

    # ── Dissertativa ──────────────────────────────────────────────────────────

    def _grade_dissertativa(
        self,
        question: str,
        answer: str,
        max_points: float,
        model_answer: str,
        rubric: str,
    ) -> GradeResult:
        context = self._dissertativa_context(question, max_points, model_answer, rubric)
        student_part = (
            f"Resposta do aluno:\n{answer}\n\n"
            f"Avalie e retorne o JSON."
        )

        raw = self._call(context, student_part, max_tokens=1024)
        data = self._parse_json(raw)

        score = min(float(data.get("score", 0)), max_points)
        percentage = (score / max_points * 100) if max_points > 0 else 0.0
        return GradeResult(
            score=score,
            max_score=max_points,
            percentage=percentage,
            feedback=data.get("feedback", ""),
            question_type="dissertativa",
        )

    def _dissertativa_context(
        self, question: str, max_points: float, model_answer: str, rubric: str
    ) -> str:
        extras = ""
        if model_answer:
            extras += f"\nGabarito/resposta esperada:\n{model_answer}"
        if rubric:
            extras += f"\nCritérios de avaliação:\n{rubric}"

        return (
            f"Questão dissertativa:\n"
            f"Enunciado: {question}{extras}\n"
            f"Pontuação máxima: {max_points}\n\n"
            f"Avalie: correção do conteúdo, completude, clareza e organização.\n"
            f'Formato obrigatório: {{"score": <0–{max_points}>, "feedback": "..."}}'
        )

    # ── Claude call with prompt caching ───────────────────────────────────────

    def _call(self, cached_context: str, student_part: str, max_tokens: int) -> str:
        """Send one grading request. The assignment context is marked for caching."""
        response = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=[
                {
                    "type": "text",
                    "text": (
                        "Você é um professor experiente e justo. "
                        "Responda SOMENTE com JSON válido, sem texto adicional."
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": [
                        # Assignment context — cached across all submissions
                        {
                            "type": "text",
                            "text": cached_context,
                            "cache_control": {"type": "ephemeral"},
                        },
                        # Student answer — changes per submission (not cached)
                        {"type": "text", "text": student_part},
                    ],
                }
            ],
        )
        return response.content[0].text

    # ── JSON parsing ──────────────────────────────────────────────────────────

    def _parse_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Strip markdown fences if present
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"score": 0, "correct": False, "feedback": "Erro ao processar resposta do modelo."}
