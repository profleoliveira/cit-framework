#!/usr/bin/env python3
"""
Corretor Automático — Google Classroom + Claude
Uso: python main.py
"""

import csv
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table

from classroom_client import ClassroomClient
from grader import Grader

load_dotenv()
console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def save_results(results: list[dict]) -> tuple[str, str]:
    out = Path("results")
    out.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    json_path = str(out / f"notas_{stamp}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    csv_path = str(out / f"notas_{stamp}.csv")
    fieldnames = ["turma", "atividade", "aluno", "nota", "nota_maxima", "percentual", "feedback"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "turma": r["course_name"],
                "atividade": r["assignment_title"],
                "aluno": r["student_name"],
                "nota": r["score"],
                "nota_maxima": r["max_score"],
                "percentual": f"{r['percentage']:.1f}%",
                "feedback": r["feedback"],
            })

    return json_path, csv_path


def pick(label: str, items: list[dict], name_key: str, extra_key: str = "") -> list[dict]:
    """Show a numbered table and return the selected subset."""
    table = Table(title=label, show_lines=False)
    table.add_column("#", style="dim", width=4)
    table.add_column("Nome")
    if extra_key:
        table.add_column(extra_key.capitalize(), justify="right")
    for i, item in enumerate(items, 1):
        row = [str(i), item.get(name_key, str(i))]
        if extra_key:
            row.append(str(item.get(extra_key, "—")))
        table.add_row(*row)
    console.print(table)

    sel = Prompt.ask("Selecione (ex: 1,3 ou [bold]todas[/bold])", default="todas")
    if sel.strip().lower() == "todas":
        return items
    indices = [int(x.strip()) - 1 for x in sel.split(",") if x.strip().isdigit()]
    return [items[i] for i in indices if 0 <= i < len(items)]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    console.rule("[bold gold1]Corretor Automático — Google Classroom + Claude[/bold gold1]")

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]ANTHROPIC_API_KEY não encontrada. Crie um arquivo .env (veja .env.example).[/red]")
        return

    credentials_file = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
    model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

    with console.status("Autenticando no Google Classroom…"):
        classroom = ClassroomClient(credentials_file)
    grader = Grader(api_key, model)

    # ── Select courses ────────────────────────────────────────────────────────
    courses = classroom.list_courses()
    if not courses:
        console.print("[yellow]Nenhuma turma ativa encontrada.[/yellow]")
        return

    selected_courses = pick("Turmas disponíveis", courses, "name", "section")
    all_results: list[dict] = []

    # ── Iterate courses → assignments → submissions ────────────────────────────
    for course in selected_courses:
        course_id = course["id"]
        course_name = course.get("name", course_id)
        console.print(f"\n[bold cyan]Turma:[/bold cyan] {course_name}")

        assignments = classroom.list_assignments(course_id)
        if not assignments:
            console.print("  [dim]Sem atividades.[/dim]")
            continue

        selected_assignments = pick(
            f"Atividades — {course_name}", assignments, "title", "maxPoints"
        )

        for assignment in selected_assignments:
            assignment_id = assignment["id"]
            title = assignment.get("title", assignment_id)
            max_points = float(assignment.get("maxPoints") or 10)
            description = assignment.get("description") or title

            console.print(f"\n  [bold]Atividade:[/bold] {title}  [dim](máx {max_points} pts)[/dim]")

            q_type = Prompt.ask(
                "  Tipo de questão",
                choices=["dissertativa", "multipla_escolha"],
                default="dissertativa",
            )
            model_answer = Prompt.ask("  Gabarito / resposta esperada [dim](Enter para pular)[/dim]", default="")
            rubric = ""
            if q_type == "dissertativa":
                rubric = Prompt.ask("  Critérios de avaliação [dim](Enter para pular)[/dim]", default="")

            submissions = classroom.list_submissions(course_id, assignment_id)
            if not submissions:
                console.print("  [dim]Sem submissões entregues.[/dim]")
                continue

            console.print(f"  [green]{len(submissions)}[/green] submissão(ões) para corrigir.")
            post_grades = Confirm.ask("  Lançar notas automaticamente no Classroom?", default=False)

            with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as bar:
                task = bar.add_task("  Corrigindo…", total=len(submissions))

                for sub in submissions:
                    student_id = sub.get("userId", "")
                    student_name = classroom.get_student_name(course_id, student_id)
                    bar.update(task, description=f"  Corrigindo {student_name}…")

                    student_answer = classroom.get_submission_text(sub)
                    try:
                        result = grader.grade(
                            question=description,
                            student_answer=student_answer,
                            max_points=max_points,
                            model_answer=model_answer,
                            rubric=rubric,
                            question_type=q_type,
                        )
                    except Exception as exc:
                        console.print(f"\n  [red]Erro ({student_name}): {exc}[/red]")
                        bar.advance(task)
                        continue

                    all_results.append({
                        "course_name": course_name,
                        "assignment_title": title,
                        "student_name": student_name,
                        "student_id": student_id,
                        "submission_id": sub["id"],
                        "score": result.score,
                        "max_score": result.max_score,
                        "percentage": result.percentage,
                        "feedback": result.feedback,
                        "question_type": result.question_type,
                    })

                    if post_grades:
                        ok = classroom.post_grade(course_id, assignment_id, sub["id"], result.score)
                        if not ok:
                            console.print(f"\n  [yellow]Aviso: não foi possível lançar nota de {student_name}.[/yellow]")

                    bar.advance(task)

    # ── Save & summary ────────────────────────────────────────────────────────
    if not all_results:
        console.print("\n[yellow]Nenhum resultado gerado.[/yellow]")
        return

    json_path, csv_path = save_results(all_results)
    console.print(f"\n[green]Correção concluída![/green]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  CSV : {csv_path}")

    summary = Table(title="Resumo por atividade")
    summary.add_column("Turma")
    summary.add_column("Atividade")
    summary.add_column("Alunos", justify="right")
    summary.add_column("Média", justify="right")

    groups: dict = defaultdict(list)
    for r in all_results:
        groups[(r["course_name"], r["assignment_title"])].append(r["percentage"])

    for (course, activity), pcts in groups.items():
        avg = sum(pcts) / len(pcts)
        color = "green" if avg >= 70 else "yellow" if avg >= 50 else "red"
        summary.add_row(course, activity, str(len(pcts)), f"[{color}]{avg:.1f}%[/{color}]")

    console.print(summary)


if __name__ == "__main__":
    main()
