"""WS8 회계사 확인용 표본 뽑기(주제마다 값 비율대로 층화)."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

from .postcheck import UNDETERMINED
from .topics import TOPIC_DEFS

DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
SAMPLE_FIELDS = ("sample_id", "topic", "rcept_no", "corp_code", "corp_name", "fiscal_year", "value", "evidence",
                 "dart_url", "excerpt")


def allocate(counts: dict[str, int], target: int) -> dict[str, int]:
    """값별 표본 수. 비율 몫의 정수부를 먼저 주고, 행이 있는 값은 최소 1건, 남는 자리는 큰 나머지
    순으로 채운다(해밀턴 방식). 행이 모자라면 있는 만큼만 준다. 합은 min(target, 전체 행 수)."""
    counts = {v: c for v, c in counts.items() if c > 0}
    total = sum(counts.values())
    target = min(target, total)
    if target <= 0:
        return {}
    exact = {v: target * c / total for v, c in counts.items()}
    q = {v: max(1, math.floor(x)) for v, x in exact.items()}
    q = {v: min(n, counts[v]) for v, n in q.items()}
    order = sorted(counts, key=lambda v: (-(exact[v] - math.floor(exact[v])), -counts[v], v))
    while sum(q.values()) < target:  # 큰 나머지 순으로 한 자리씩(행이 남은 값만)
        for v in order:
            if sum(q.values()) >= target:
                break
            if q[v] < counts[v]:
                q[v] += 1
    while sum(q.values()) > target:  # 최소 1건 때문에 넘치면 몫이 큰 값에서 작은 나머지 순으로 뺀다
        over = [v for v in q if q[v] > 1]
        if not over:  # 값 종류가 target보다 많은 경우(이번 8개 주제에는 없다): 값마다 1건
            break
        v = max(over, key=lambda v: (q[v] - exact[v], q[v], v))
        q[v] -= 1
    return q


def draw_samples(rows: list[dict[str, Any]], *, per_topic: int = 20, seed: int = 20261010) -> list[dict[str, Any]]:
    """주제마다 UNDETERMINED를 뺀 행에서 값 비율대로 `per_topic`건을 뽑는다.

    입력 순서와 무관하게 재현된다(rcept_no로 정렬한 뒤 `seed:topic:value` 난수로 뽑는다).
    행에 corp_name·excerpt가 있으면 옮기고, 없으면 빈 문자열이다."""
    by_tv: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("value") and r["value"] != UNDETERMINED:
            by_tv[r["topic"]][r["value"]].append(r)
    topic_order = [t for t in TOPIC_DEFS if t in by_tv] + sorted(t for t in by_tv if t not in TOPIC_DEFS)
    out: list[dict[str, Any]] = []
    for topic in topic_order:
        groups = by_tv[topic]
        quota = allocate({v: len(rs) for v, rs in groups.items()}, per_topic)
        values = list(TOPIC_DEFS[topic].values) if topic in TOPIC_DEFS else []
        picked: list[dict[str, Any]] = []
        for value in sorted(quota, key=lambda v: (values.index(v) if v in values else len(values), v)):
            pool = sorted(groups[value], key=lambda r: r["rcept_no"])
            rng = random.Random(f"{seed}:{topic}:{value}")
            picked += sorted(rng.sample(pool, quota[value]), key=lambda r: r["rcept_no"])
        for i, r in enumerate(picked, 1):
            out.append({
                "sample_id": f"{topic}-{i:02d}",
                "topic": topic,
                "rcept_no": r["rcept_no"],
                "corp_code": r.get("corp_code"),
                "corp_name": r.get("corp_name") or "",
                "fiscal_year": r.get("fiscal_year"),
                "value": r["value"],
                "evidence": r.get("evidence", ""),
                "dart_url": DART_URL.format(rcept_no=r["rcept_no"]),
                "excerpt": r.get("excerpt") or "",
            })
    return out
