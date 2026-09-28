"""GOV-C2-046 — deterministic domain services (no framework imports).

`ShelterKB` normalises authorised evacuation-shelter intake, applies record-level access scoping, and
maps occupant support/medical notes onto the 要配慮者 (vulnerable-resident) accessibility taxonomy. It
holds a seeded reference of the taxonomy, facility-feature → capability mapping, and resource-option
templates (supply / staffing / transfer) — all sourced from public disaster-response guidance
(災害対策基本法 / 避難行動要支援者名簿制度), so classification and option scoring are deterministic and
auditable. The template performs no LLM inference (no model dependency, no model call).

`AllocationEngine` synthesises a prioritised allocation + escalation plan and runs the **fail-closed
HumanGate review**: on low confidence, data gaps, or critical resource contention it withholds the
allocation recommendation and escalates to authorised incident-command review. The engine never
executes a real allocation, dispatch, or evacuation order — it produces an advisory draft only.
"""

from __future__ import annotations

import re
from typing import Any

# ── S-1/S-3 field-level PII / secret redaction (deterministic, no framework imports) ─────────
# Single source of truth for the redaction patterns used by pre_process (S-1, before a field is
# persisted to State / validated_input) and post_process (S-3, before egress). The triage workflow
# only ever needs the aggregate accessibility-need signal, so any resident direct-identifier / secret
# that a record inadvertently carries is masked; the need-marker keywords are Japanese words (not these
# patterns), so classification is unaffected.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_MY_NUMBER = re.compile(r"(?<![\d.])\d{12}(?![\d.])")  # 個人番号 (12 digits)
_CREDENTIAL = re.compile(
    r"sk-[A-Za-z0-9_\-]{8,}"  # OpenAI-style secret key
    r"|AKIA[0-9A-Z]{12,}"  # AWS access key id
    r"|eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{4,}"  # JWT (header.payload.sig)
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,3}\.[A-Za-z]{2,24}")
_PHONE = re.compile(
    r"\+81[-\s]?\d{1,4}[-\s]?\d{1,4}[-\s]?\d{3,4}"  # +81 international
    r"|0\d{1,3}-\d{2,4}-\d{3,4}"  # hyphenated JP landline / mobile
    r"|0[789]0\d{8}"  # 11-digit mobile, no separators
)
_MY_NUMBER_MASK = "[MY-NUMBER-REDACTED]"
_CREDENTIAL_MASK = "[CREDENTIAL-REDACTED]"
_EMAIL_MASK = "[EMAIL-REDACTED]"
_PHONE_MASK = "[PHONE-REDACTED]"

# Occupant record fields the accessibility-triage workflow actually consumes. Everything else on an
# occupant (name / phone / email / address / My-Number / guardian ...) is a direct identifier the agent
# never needs — dropped at S-1 so no vulnerable-resident PII is persisted to State (design: aggregate
# counts only; classify_needs reads only these two note fields).
OCCUPANT_NEED_FIELDS: tuple[str, ...] = ("support_notes", "medical_note")


def redact_pii(text: str) -> str:
    """S-1/S-3 hygiene: strip control chars and mask secrets / direct-identifier patterns.

    Redacts credentials (sk-*/AKIA*/JWT), email, My-Number, and phone in a fixed order (control →
    credential → email → My-Number → phone) so a longer/more specific pattern wins first — in particular
    the 12-digit My-Number is masked before the 10-11 digit phone rule can partially consume it.
    """
    out = _CONTROL.sub("", text or "")
    out = _CREDENTIAL.sub(_CREDENTIAL_MASK, out)
    out = _EMAIL.sub(_EMAIL_MASK, out)
    out = _MY_NUMBER.sub(_MY_NUMBER_MASK, out)
    return _PHONE.sub(_PHONE_MASK, out)


# ── accessibility taxonomy (要配慮者カテゴリ) ─────────────────────────────────────
# category -> (label, severity weight, keyword markers). Severity 5 = life-critical.
_TAXONOMY: dict[str, dict[str, Any]] = {
    "medical_care": {
        "label": "医療的ケア",
        "severity": 5,
        "markers": [
            "医療",
            "酸素",
            "透析",
            "服薬",
            "たん吸引",
            "インスリン",
            "医療的ケア",
            "呼吸器",
            "medical",
            "oxygen",
            "dialysis",
        ],
    },
    "wheelchair": {
        "label": "車椅子・移動支援",
        "severity": 4,
        "markers": ["車椅子", "車いす", "歩行困難", "移動支援", "移動介助", "寝たきり", "wheelchair", "mobility"],
    },
    "elderly_care": {
        "label": "要介護高齢者",
        "severity": 4,
        "markers": ["要介護", "高齢", "認知症", "介助", "おむつ", "elderly", "care"],
    },
    "pregnant": {
        "label": "妊産婦",
        "severity": 3,
        "markers": ["妊婦", "妊産婦", "産前", "産後", "pregnant", "maternity"],
    },
    "infant": {
        "label": "乳幼児",
        "severity": 3,
        "markers": ["乳児", "幼児", "授乳", "ミルク", "おむつ交換", "infant", "baby"],
    },
    "sensory": {
        "label": "視覚・聴覚",
        "severity": 3,
        "markers": ["視覚", "聴覚", "全盲", "弱視", "難聴", "ろう", "手話", "blind", "deaf", "hearing"],
    },
    "cognitive": {
        "label": "認知・知的・精神",
        "severity": 3,
        "markers": ["知的", "発達", "精神", "パニック", "cognitive", "autism"],
    },
    "language": {
        "label": "外国人・言語支援",
        "severity": 2,
        "markers": ["外国人", "日本語", "通訳", "やさしい日本語", "language", "foreign", "interpreter"],
    },
}

# facility feature -> categories the feature supports in-place
_FEATURE_SUPPORT: dict[str, tuple[str, ...]] = {
    "medical_room": ("medical_care",),
    "nurse_station": ("medical_care", "elderly_care"),
    "generator": ("medical_care",),  # powers oxygen concentrators, etc.
    "barrier_free_toilet": ("wheelchair", "elderly_care"),
    "elevator": ("wheelchair",),
    "ramp": ("wheelchair",),
    "ground_floor": ("wheelchair", "elderly_care"),
    "welfare_shelter": ("wheelchair", "elderly_care", "medical_care", "cognitive"),  # 福祉避難所
    "nursing_room": ("infant", "pregnant"),
    "quiet_space": ("cognitive", "sensory"),
}

# category -> in-template resolution template (option_type, phrasing, whether transfer is a material escalation)
_RESOLUTION: dict[str, dict[str, Any]] = {
    "medical_care": {
        "option_type": "staffing",
        "detail": "看護要員・医療的ケア対応の配置",
        "escalate_option": "transfer",
        "escalate_detail": "医療対応可能な福祉避難所への移送検討",
        "critical": True,
    },
    "wheelchair": {
        "option_type": "supply",
        "detail": "簡易スロープ・車椅子対応スペースの確保",
        "escalate_option": "transfer",
        "escalate_detail": "バリアフリー避難所への移送検討",
        "critical": False,
    },
    "elderly_care": {
        "option_type": "staffing",
        "detail": "介護要員・介助スペースの配置",
        "escalate_option": "transfer",
        "escalate_detail": "福祉避難所への移送検討",
        "critical": False,
    },
    "pregnant": {
        "option_type": "supply",
        "detail": "母子用スペース・衛生用品の確保",
        "escalate_option": "staffing",
        "escalate_detail": "保健要員の配置検討",
        "critical": False,
    },
    "infant": {
        "option_type": "supply",
        "detail": "ミルク・おむつ・授乳スペースの確保",
        "escalate_option": "staffing",
        "escalate_detail": "保健要員の配置検討",
        "critical": False,
    },
    "sensory": {
        "option_type": "staffing",
        "detail": "手話・情報保障・案内支援の配置",
        "escalate_option": "supply",
        "escalate_detail": "筆談・掲示等の情報保障手段の確保",
        "critical": False,
    },
    "cognitive": {
        "option_type": "staffing",
        "detail": "静養スペース・見守り支援の配置",
        "escalate_option": "transfer",
        "escalate_detail": "福祉避難所への移送検討",
        "critical": False,
    },
    "language": {
        "option_type": "staffing",
        "detail": "通訳・多言語/やさしい日本語掲示の配置",
        "escalate_option": "supply",
        "escalate_detail": "多言語掲示物の確保",
        "critical": False,
    },
}

TAXONOMY_SOURCE = "避難行動要支援者名簿制度 / 災害対策基本法（要配慮者 accessibility taxonomy）"

# fail-closed HumanGate thresholds
_LOW_CONFIDENCE_RATIO = 0.5  # >50% occupants unclassifiable → ambiguous needs → escalate

_APPROVER_PLACEHOLDER = "PLACEHOLDER — 認可済み災害対策本部 / incident commander（確定は HumanGate の人手のみ）"


class ShelterKB:
    """Deterministic ingest, access scoping, needs classification, and option evaluation."""

    @staticmethod
    def normalize_records(intake: Any) -> list[dict[str, Any]]:
        """Coerce raw intake into a list of structured shelter records."""
        if isinstance(intake, dict):
            intake = intake.get("shelters") or intake.get("records") or [intake]
        if not isinstance(intake, list):
            return []
        out: list[dict[str, Any]] = []
        for i, raw in enumerate(intake):
            if not isinstance(raw, dict):
                continue
            occupants = raw.get("occupants") or []
            if not isinstance(occupants, list):
                occupants = []
            out.append(
                {
                    "shelter_id": str(raw.get("shelter_id") or raw.get("id") or f"SH-{i + 1:03d}"),
                    "name": str(raw.get("name") or ""),
                    "capacity": raw.get("capacity"),
                    "occupancy": raw.get("occupancy"),
                    "facility_features": [str(f) for f in (raw.get("facility_features") or []) if isinstance(f, str)],
                    "occupants": [o for o in occupants if isinstance(o, dict)],
                    "provenance": str(raw.get("provenance") or "unknown"),
                }
            )
        return out

    @staticmethod
    def access_scope(records: list[dict[str, Any]], authorized: list[str] | None) -> tuple[list[dict[str, Any]], int]:
        """S-2 record-level access scoping: keep only records whose provenance is authorised.

        An empty / absent allowlist means the caller vouches for the whole authorised batch (no drop).
        """
        if not authorized:
            return list(records), 0
        allow = {str(a) for a in authorized}
        kept = [r for r in records if r.get("provenance") in allow]
        return kept, len(records) - len(kept)

    @staticmethod
    def _classify_note(text: str) -> list[str]:
        low = (text or "").lower()
        hits: list[str] = []
        for cat, meta in _TAXONOMY.items():
            if any(m.lower() in low for m in meta["markers"]):
                hits.append(cat)
        return hits

    @classmethod
    def classify_needs(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Classify occupant notes into the accessibility taxonomy. Returns per-category counts + confidence."""
        counts: dict[str, int] = {}
        occupants = record.get("occupants") or []
        unclassified = 0
        for occ in occupants:
            text = f"{occ.get('support_notes', '')} {occ.get('medical_note', '')}"
            cats = cls._classify_note(text)
            if not cats and text.strip():
                unclassified += 1
            for c in cats:
                counts[c] = counts.get(c, 0) + 1
        needs = [
            {
                "category": c,
                "label": _TAXONOMY[c]["label"],
                "severity": _TAXONOMY[c]["severity"],
                "count": n,
                "evidence": f"{n}件の intake note が {_TAXONOMY[c]['label']} に該当",
            }
            for c, n in sorted(counts.items(), key=lambda kv: (-_TAXONOMY[kv[0]]["severity"], kv[0]))
            if n > 0
        ]
        return {
            "shelter_id": record["shelter_id"],
            "needs": needs,
            "occupant_total": len(occupants),
            "unclassified": unclassified,
        }

    @staticmethod
    def _feature_supports(features: list[str], category: str) -> bool:
        for f in features:
            if category in _FEATURE_SUPPORT.get(f, ()):  # noqa: SIM118 - dict.get default tuple
                return True
        return False

    @classmethod
    def evaluate_options(cls, record: dict[str, Any], classified: dict[str, Any]) -> dict[str, Any]:
        """Score supply/staffing/transfer options for each classified need × facility constraint."""
        features = record.get("facility_features") or []
        cap = record.get("capacity")
        occ = record.get("occupancy")
        headroom = None if not isinstance(cap, int) or not isinstance(occ, int) else cap - occ
        options: list[dict[str, Any]] = []
        for need in classified.get("needs", []):
            cat = need["category"]
            res = _RESOLUTION[cat]
            in_place = cls._feature_supports(features, cat)
            # score = severity × count (capped headroom pressure raises priority when crowded)
            score = need["severity"] * need["count"]
            if headroom is not None and headroom <= 0:
                score += need["severity"]  # over-capacity shelter → escalate priority
            if in_place:
                options.append(
                    {
                        "category": cat,
                        "label": need["label"],
                        "count": need["count"],
                        "severity": need["severity"],
                        "option_type": "in_place",
                        "detail": f"施設内対応可（{res['detail']}）",
                        "feasible": True,
                        "escalation": False,
                        "score": score,
                    }
                )
            else:
                escalate = res["escalate_option"] == "transfer"
                options.append(
                    {
                        "category": cat,
                        "label": need["label"],
                        "count": need["count"],
                        "severity": need["severity"],
                        "option_type": res["escalate_option"] if escalate else res["option_type"],
                        "detail": res["escalate_detail"] if escalate else res["detail"],
                        "feasible": not escalate,
                        "escalation": escalate,
                        "critical": bool(res["critical"]) and escalate,
                        "score": score,
                    }
                )
        return {"shelter_id": record["shelter_id"], "options": options}


class AllocationEngine:
    """Prioritised allocation synthesis + fail-closed HumanGate review (advisory only)."""

    @staticmethod
    def synthesize(evaluations: list[dict[str, Any]]) -> dict[str, Any]:
        """Rule/threshold ranking of options into prioritised allocation + escalations (deterministic)."""
        priorities: list[dict[str, Any]] = []
        escalations: list[dict[str, Any]] = []
        for ev in evaluations:
            sid = ev["shelter_id"]
            for opt in ev.get("options", []):
                if opt.get("escalation"):
                    escalations.append(
                        {
                            "shelter_id": sid,
                            "category": opt["category"],
                            "label": opt["label"],
                            "option_type": opt["option_type"],
                            "reason": opt["detail"],
                            "critical": bool(opt.get("critical")),
                            "score": opt["score"],
                        }
                    )
                else:
                    priorities.append(
                        {
                            "shelter_id": sid,
                            "category": opt["category"],
                            "label": opt["label"],
                            "option_type": opt["option_type"],
                            "action": opt["detail"],
                            "score": opt["score"],
                        }
                    )
        priorities.sort(key=lambda p: (-p["score"], p["shelter_id"], p["category"]))
        for rank, p in enumerate(priorities, start=1):
            p["rank"] = rank
        escalations.sort(key=lambda e: (-e["score"], e["shelter_id"], e["category"]))
        return {"priorities": priorities, "escalations": escalations}

    @staticmethod
    def human_gate_review(
        records: list[dict[str, Any]], classified: list[dict[str, Any]], plan: dict[str, Any]
    ) -> dict[str, Any]:
        """Fail-closed HumanGate: withhold the allocation recommendation on low confidence / data gap /
        critical resource contention and escalate to authorised incident-command review.

        Returns ``decision`` = "escalation_only" (fail_closed) | "human_review_required". A human approver
        is ALWAYS required (advisory only); fail_closed additionally suppresses the allocation output.
        """
        reasons: list[str] = []

        # data gap — missing / non-positive capacity or occupancy blocks constraint evaluation
        for r in records:
            cap, occ = r.get("capacity"), r.get("occupancy")
            if not isinstance(cap, int) or cap <= 0 or not isinstance(occ, int) or occ < 0:
                reasons.append(f"data_gap: {r['shelter_id']} の capacity/occupancy が欠落または不正")

        # low confidence — too many unclassifiable occupant notes
        total = sum(c.get("occupant_total", 0) for c in classified)
        uncl = sum(c.get("unclassified", 0) for c in classified)
        if total > 0 and (uncl / total) > _LOW_CONFIDENCE_RATIO:
            reasons.append(f"low_confidence: 分類不能の要配慮 note が {uncl}/{total} 件（>50%）")

        # critical resource contention — a life-critical need requiring inter-shelter transfer
        for esc in plan.get("escalations", []):
            if esc.get("critical"):
                reasons.append(f"resource_contention: {esc['shelter_id']} の {esc['label']} が施設内対応不可（移送要）")

        fail_closed = bool(reasons)
        return {
            "decision": "escalation_only" if fail_closed else "human_review_required",
            "fail_closed": fail_closed,
            "reasons": reasons,
            "exceptions": reasons if fail_closed else [],
            "required_human_approver": _APPROVER_PLACEHOLDER,
        }
