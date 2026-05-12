import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

TOKEN_FILE = "token.pickle"


class ClassroomClient:
    def __init__(self, credentials_file: str = "credentials.json"):
        self._authenticate(credentials_file)

    def _authenticate(self, credentials_file: str) -> None:
        creds = None
        if Path(TOKEN_FILE).exists():
            with open(TOKEN_FILE, "rb") as f:
                creds = pickle.load(f)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, "wb") as f:
                pickle.dump(creds, f)

        self._classroom = build("classroom", "v1", credentials=creds)
        self._drive = build("drive", "v3", credentials=creds)

    # ── Courses ──────────────────────────────────────────────────────────────

    def list_courses(self) -> list[dict]:
        courses, page_token = [], None
        while True:
            res = self._classroom.courses().list(
                teacherId="me",
                courseStates=["ACTIVE"],
                pageToken=page_token,
            ).execute()
            courses.extend(res.get("courses", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        return courses

    # ── Assignments ──────────────────────────────────────────────────────────

    def list_assignments(self, course_id: str) -> list[dict]:
        assignments, page_token = [], None
        while True:
            res = self._classroom.courses().courseWork().list(
                courseId=course_id,
                pageToken=page_token,
            ).execute()
            assignments.extend(res.get("courseWork", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        return assignments

    # ── Submissions ──────────────────────────────────────────────────────────

    def list_submissions(self, course_id: str, assignment_id: str) -> list[dict]:
        submissions, page_token = [], None
        while True:
            res = (
                self._classroom.courses()
                .courseWork()
                .studentSubmissions()
                .list(
                    courseId=course_id,
                    courseWorkId=assignment_id,
                    states=["TURNED_IN"],
                    pageToken=page_token,
                )
                .execute()
            )
            submissions.extend(res.get("studentSubmissions", []))
            page_token = res.get("nextPageToken")
            if not page_token:
                break
        return submissions

    def get_submission_text(self, submission: dict) -> str:
        """Extract plain text from a submission (short answer or Drive attachment)."""
        # Short answer (Google Forms-style)
        if "shortAnswerSubmission" in submission:
            return submission["shortAnswerSubmission"].get("answer", "")

        # Drive attachments
        attachments = (
            submission.get("assignmentSubmission", {}).get("attachments", [])
        )
        texts = []
        for att in attachments:
            if "driveFile" in att:
                text = self._read_drive_file(att["driveFile"]["id"])
                if text:
                    texts.append(text)
        return "\n\n".join(texts)

    def _read_drive_file(self, file_id: str) -> str:
        # Try to export as plain text (works for Google Docs)
        try:
            content = self._drive.files().export(
                fileId=file_id, mimeType="text/plain"
            ).execute()
            return content.decode("utf-8") if isinstance(content, bytes) else content
        except HttpError:
            pass
        # Fallback: download raw (for .txt, .md, etc.)
        try:
            content = self._drive.files().get_media(fileId=file_id).execute()
            return content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        except HttpError:
            return ""

    # ── Students ─────────────────────────────────────────────────────────────

    def get_student_name(self, course_id: str, user_id: str) -> str:
        try:
            profile = (
                self._classroom.courses()
                .students()
                .get(courseId=course_id, userId=user_id)
                .execute()
            )
            return profile.get("profile", {}).get("name", {}).get("fullName", user_id)
        except HttpError:
            return user_id

    # ── Grading ───────────────────────────────────────────────────────────────

    def post_grade(
        self,
        course_id: str,
        assignment_id: str,
        submission_id: str,
        grade: float,
    ) -> bool:
        try:
            self._classroom.courses().courseWork().studentSubmissions().patch(
                courseId=course_id,
                courseWorkId=assignment_id,
                id=submission_id,
                updateMask="assignedGrade,draftGrade",
                body={"assignedGrade": grade, "draftGrade": grade},
            ).execute()
            return True
        except HttpError:
            return False
