"""Publishers (ported from oxcorp-loop's interfaces/publisher.py, adapted to
clipper's dict-shaped clip rows from loop_store).

DryRunPublisher is the DEFAULT — it logs the exact payload and posts nothing.
A real Reel goes out only behind an explicit --live flag, to a consented
Business/Creator account, via the official Graph API. Guardrails G1/G4:
sanctioned APIs only; this module is the ONLY place anything leaves the system.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from .config import Settings
from . import loop_store


@dataclass
class PublishPlan:
    """Where/how a clip should go out."""
    platform: str                 # instagram
    account_ref: str              # the consented account handle/id
    mode: str = "dryrun"          # dryrun | live
    format: str = "9:16"          # clipper renders vertical only
    scheduled_for: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class PublishResult:
    platform: str
    mode: str
    status: str                   # dryrun | published | failed
    external_id: Optional[str] = None
    permalink: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)


class Publisher(ABC):
    @abstractmethod
    def publish(self, clip: dict, plan: PublishPlan) -> PublishResult: ...


class DryRunPublisher(Publisher):
    """Default: logs the full payload, posts nothing. The safety net."""

    def publish(self, clip: dict, plan: PublishPlan) -> PublishResult:
        payload = {
            "platform": plan.platform,
            "account_ref": plan.account_ref,
            "format": plan.format,
            "file": clip.get("path"),
            "caption": _full_caption(clip),
            "hook": clip.get("hook", ""),
        }
        return PublishResult(plan.platform, "dryrun", "dryrun", detail={"would_post": payload})


class InstagramPublisher(Publisher):
    """Live Instagram Reels via the Graph API two-step container flow
    (create -> poll until FINISHED -> publish). Only invoked with --live to a
    consented Business/Creator account. The Graph API fetches the video from a
    PUBLIC url, so clips must be reachable at PUBLIC_MEDIA_BASE_URL/media/<clip_id>
    (served by clipper/service.py, exposed e.g. via a tunnel).
    """

    def __init__(self, ig_user_id: str, access_token: str, public_media_base_url: str,
                 graph_version: str = "v21.0", poll_timeout: float = 180.0, poll_interval: float = 5.0):
        self.ig_user_id = ig_user_id
        self.token = access_token
        self.base_url = public_media_base_url.rstrip("/")
        self.api = f"https://graph.facebook.com/{graph_version}"
        self.poll_timeout = poll_timeout
        self.poll_interval = poll_interval

    def publish(self, clip: dict, plan: PublishPlan) -> PublishResult:
        import requests

        if not (self.ig_user_id and self.token):
            raise RuntimeError("InstagramPublisher needs IG_USER_ID and IG_ACCESS_TOKEN")
        if not self.base_url:
            raise RuntimeError("PUBLIC_MEDIA_BASE_URL must host the clip so the Graph API can fetch it")
        if not clip.get("path"):
            raise RuntimeError("clip has no rendered file to publish")

        video_url = f"{self.base_url}/media/{clip['id']}"

        # 1) create media container
        create = requests.post(
            f"{self.api}/{self.ig_user_id}/media",
            data={"media_type": "REELS", "video_url": video_url,
                  "caption": _full_caption(clip), "access_token": self.token},
            timeout=30,
        ).json()
        if "id" not in create:
            return PublishResult(plan.platform, "live", "failed", detail={"create": create})
        container = create["id"]

        # 2) poll until the container is FINISHED
        deadline = time.monotonic() + self.poll_timeout
        status = "IN_PROGRESS"
        while time.monotonic() < deadline:
            st = requests.get(
                f"{self.api}/{container}",
                params={"fields": "status_code", "access_token": self.token}, timeout=30,
            ).json()
            status = st.get("status_code", "ERROR")
            if status in ("FINISHED", "ERROR", "EXPIRED"):
                break
            time.sleep(self.poll_interval)
        if status != "FINISHED":
            return PublishResult(plan.platform, "live", "failed", detail={"status_code": status})

        # 3) publish the container
        pub = requests.post(
            f"{self.api}/{self.ig_user_id}/media_publish",
            data={"creation_id": container, "access_token": self.token}, timeout=30,
        ).json()
        media_id = pub.get("id")
        if not media_id:
            return PublishResult(plan.platform, "live", "failed", detail={"publish": pub})

        permalink = requests.get(
            f"{self.api}/{media_id}",
            params={"fields": "permalink", "access_token": self.token}, timeout=30,
        ).json().get("permalink")
        return PublishResult(plan.platform, "live", "published",
                             external_id=media_id, permalink=permalink)


def _full_caption(clip: dict) -> str:
    parts = [p for p in (clip.get("hook", ""), clip.get("caption", "")) if p]
    return "\n\n".join(parts)[:2200]


def _publisher_for(s: Settings, plan: PublishPlan) -> Publisher:
    if plan.mode != "live":
        return DryRunPublisher()
    return InstagramPublisher(s.ig_user_id, s.ig_access_token, s.public_media_base_url)


def publish_clips(s: Settings, clip_ids: list[str], *, live: bool = False,
                  platform: Optional[str] = None) -> list[dict]:
    """Publish each clip (by loop-store id) and record every attempt in `publishes`.
    Dry-run unless live=True. Returns the recorded publish rows."""
    platform = platform or s.target_platform
    mode = "live" if live else "dryrun"
    plan = PublishPlan(platform=platform, account_ref=s.ig_user_id or "self", mode=mode)
    publisher = _publisher_for(s, plan)
    recorded = []

    for cid in clip_ids:
        clip = loop_store.get_clip(s.data, cid)
        if not clip:
            print(f"[publish] no such clip: {cid}")
            continue
        try:
            res = publisher.publish(clip, plan)
        except Exception as e:  # one failure shouldn't kill the batch
            loop_store.insert_publish(s.data, clip_id=cid, platform=platform, mode=mode,
                                      status="failed", detail={"error": str(e)})
            print(f"[publish] clip {cid} FAILED: {e}")
            continue
        loop_store.insert_publish(
            s.data, clip_id=cid, platform=res.platform, mode=res.mode, status=res.status,
            external_id=res.external_id, permalink=res.permalink, detail=res.detail)
        recorded.append({"clip_id": cid, "status": res.status, "mode": res.mode,
                         "permalink": res.permalink})
        if res.status == "published":
            print(f"[publish] clip {cid} PUBLISHED -> {res.permalink or res.external_id}")
        elif res.status == "dryrun":
            print(f"[publish] clip {cid} dry-run (payload logged to DB, nothing posted)")
        else:
            print(f"[publish] clip {cid} {res.status}")
    return recorded
