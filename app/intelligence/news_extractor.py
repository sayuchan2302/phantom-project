"""
News Extractor using Gemini API.
Strictly extracts qualitative economic mechanism and relies on deterministic Python code
for quantitative metrics (Surprise Score). Guarantees graceful degradation on failure.
"""
import json
from datetime import datetime, timezone
from typing import Optional
from google import genai
from google.genai import types

from app.config import settings
from app.core.schemas import EconomicEvent, NewsExtractionResult
from app.monitoring.logger import app_logger


class NewsExtractor:
    """Extracts macroeconomic context using Gemini API with robust fallbacks."""

    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3.8-flash"):
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.model_name = model_name
        self.client = None
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                app_logger.warning(f"Could not initialize Gemini Client: {e}")

    @staticmethod
    def calculate_surprise_score(actual: Optional[float], forecast: Optional[float]) -> Optional[float]:
        """
        Calculates surprise score strictly in Python code:
        Surprise Score = (Actual - Forecast) / |Forecast|
        """
        if actual is None or forecast is None:
            return None
        if abs(forecast) < 1e-6:
            # When forecast is near zero, difference directly
            return round(actual - forecast, 4)
        return round((actual - forecast) / abs(forecast), 4)

    def extract_from_event(self, event: EconomicEvent) -> NewsExtractionResult:
        """
        Process an economic event, calculate surprise score deterministically,
        and get qualitative market impact via Gemini (or fallback).
        """
        surprise = self.calculate_surprise_score(event.actual, event.forecast)
        qualitative_impact = self._get_qualitative_impact(event, surprise)

        return NewsExtractionResult(
            event_name=event.name,
            time_utc=event.time_utc,
            actual=event.actual,
            forecast=event.forecast,
            previous=event.previous,
            surprise_score=surprise,
            qualitative_impact=qualitative_impact,
            source_model=self.model_name if self.client else "rule-based-fallback",
        )

    def _get_qualitative_impact(self, event: EconomicEvent, surprise: Optional[float]) -> str:
        """Query Gemini API for macro mechanism, with instant rule-based fallback."""
        if not self.client:
            return self._fallback_impact(event, surprise)

        prompt = (
            f"Analyze the macroeconomic relationship for this economic event:\n"
            f"Event: {event.name} ({event.currency})\n"
            f"Actual: {event.actual}, Forecast: {event.forecast}, Previous: {event.previous}\n"
            f"Surprise score: {surprise}\n\n"
            f"Task: In 1-2 concise sentences, state the standard macroeconomic impact on the US Dollar (USD) "
            f"and Gold (XAU/USD). Do NOT predict winning probabilities or trade signals. "
            f"Only describe the economic mechanism (e.g., Higher inflation -> Hawkish Fed expectations -> Stronger USD / Downward pressure on Gold)."
        )

        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=1024,
                ),
            )
            if response and response.text:
                return response.text.strip()
        except Exception as e:
            app_logger.warning(f"Gemini API call failed or timed out: {e}. Degrading to fallback impact.")

        return self._fallback_impact(event, surprise)

    @staticmethod
    def _fallback_impact(event: EconomicEvent, surprise: Optional[float]) -> str:
        """Deterministic rule-based summary when LLM is unavailable."""
        event_lower = event.name.lower()
        if surprise is None:
            return f"{event.name} scheduled for release. Market typically consolidates ahead of high-impact data."

        if any(k in event_lower for k in ["cpi", "ppi", "inflation"]):
            if surprise > 0:
                return "Higher inflation than expected typically strengthens USD (hawkish expectations), creating downward pressure on Gold."
            else:
                return "Lower inflation than expected typically weakens USD, providing upward support for Gold."
        elif any(k in event_lower for k in ["nfp", "non-farm", "employment", "job"]):
            if surprise > 0:
                return "Stronger jobs data supports USD strength, potentially weighing on Gold."
            else:
                return "Weaker jobs data increases rate cut expectations, generally supportive for Gold."
        else:
            direction = "above" if surprise > 0 else "below"
            return f"{event.name} came in {direction} consensus forecast."
