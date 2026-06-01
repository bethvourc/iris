from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class OcrResult:
    text: str


@dataclass(frozen=True)
class LabelResult:
    description: str
    score: float


class GoogleVisionClient:
    def __init__(self, credentials_path: str | None) -> None:
        self.credentials_path = credentials_path
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.credentials_path)

    def _get_client(self):
        if not self.credentials_path:
            raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS is not configured")
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", self.credentials_path)
        if self._client is None:
            from google.cloud import vision  # type: ignore

            self._client = vision.ImageAnnotatorClient()
        return self._client

    def extract_text(self, image_bytes: bytes) -> OcrResult:
        from google.cloud import vision  # type: ignore

        client = self._get_client()
        image = vision.Image(content=image_bytes)
        response = client.document_text_detection(image=image)
        if response.error.message:
            raise RuntimeError(response.error.message)
        annotation = response.full_text_annotation
        return OcrResult(text=annotation.text if annotation else "")

    def label_image(
        self, image_bytes: bytes, max_results: int = 10
    ) -> list[LabelResult]:
        from google.cloud import vision  # type: ignore

        client = self._get_client()
        image = vision.Image(content=image_bytes)
        response = client.label_detection(image=image, max_results=max_results)
        if response.error.message:
            raise RuntimeError(response.error.message)
        return [
            LabelResult(description=label.description, score=float(label.score))
            for label in response.label_annotations
        ]
