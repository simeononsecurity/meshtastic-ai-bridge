"""Small retrieval adapter interface used by the bridge.

Adapters return source-labeled context or an empty string. They never decide
what the model should do and they never expose credentials in their output.
"""

import json
import os
from abc import ABC, abstractmethod
from urllib.parse import quote_plus

import requests


class RetrievalAdapter(ABC):
    name = "adapter"

    @abstractmethod
    def handles(self, command):
        """Return whether this adapter handles a normalized command."""

    @abstractmethod
    def retrieve(self, value):
        """Return source-labeled context or an empty string."""


class WeatherAdapter(RetrievalAdapter):
    name = "weather"

    def __init__(self, enabled, location="", user_agent=""):
        self.enabled = enabled
        self.location = location
        self.user_agent = user_agent

    def handles(self, command):
        return command == "weather"

    def retrieve(self, value):
        if not self.enabled:
            return ""
        location = value or self.location
        if not location:
            return "No weather location configured. Ask with: weather <city or ZIP>."
        response = requests.get(
            f"https://wttr.in/{quote_plus(location)}",
            params={"format": "3"},
            headers={"User-Agent": self.user_agent},
            timeout=8,
        )
        response.raise_for_status()
        return f"SOURCE: wttr.in\n{response.text.strip()}"


class NewsAdapter(RetrievalAdapter):
    name = "news"

    def __init__(self, enabled, topic="", user_agent=""):
        self.enabled = enabled
        self.topic = topic
        self.user_agent = user_agent

    def handles(self, command):
        return command == "news"

    def retrieve(self, value):
        if not self.enabled:
            return ""
        topic = value or self.topic
        if not topic:
            return "No news topic configured. Ask with: news <topic>."
        response = requests.get(
            "https://news.google.com/rss/search",
            params={"q": topic, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"User-Agent": self.user_agent},
            timeout=8,
        )
        response.raise_for_status()
        titles = []
        for title in response.text.split("<title>")[1:6]:
            titles.append(title.split("</title>", 1)[0].replace("<![CDATA[", "").replace("]]>", ""))
        return "SOURCE: Google News RSS\n" + "\n".join(titles[1:6])


class MedicalReferenceAdapter(RetrievalAdapter):
    name = "medical_reference"

    def __init__(self, search_local):
        self.search_local = search_local

    def handles(self, command):
        return command in ("medical", "medicine", "health", "firstaid", "reference")

    def retrieve(self, value):
        return self.search_local(value) if value else ""


class SensorAdapter(RetrievalAdapter):
    """Optional local JSON sensor endpoint; disabled unless SENSOR_URL is set."""
    name = "sensor"

    def __init__(self, url="", user_agent=""):
        self.url = url.rstrip("/")
        self.user_agent = user_agent

    def handles(self, command):
        return command in ("sensor", "sensors", "telemetry")

    def retrieve(self, value):
        if not self.url:
            return "No local sensor adapter is configured."
        response = requests.get(self.url, headers={"User-Agent": self.user_agent}, timeout=5)
        response.raise_for_status()
        data = response.json()
        return "SOURCE: local sensor adapter\n" + json.dumps(data, separators=(",", ":"))[:1800]


def build_adapters(enabled, user_agent, weather_location, news_topic, local_search):
    return [
        WeatherAdapter(enabled, weather_location, user_agent),
        NewsAdapter(enabled, news_topic, user_agent),
        MedicalReferenceAdapter(local_search),
        SensorAdapter(os.environ.get("SENSOR_URL", ""), user_agent),
    ]