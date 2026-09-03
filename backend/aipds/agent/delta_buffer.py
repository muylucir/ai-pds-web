# backend/aipds/agent/delta_buffer.py — 토큰 델타를 공백 경계까지만 흘린다.
#
# **왜 필요한가.** 부분 메시지 스트리밍(`include_partial_messages`)을 켜면 어시스턴트
# 텍스트가 평균 4~5자짜리 `text_delta`로 쪼개져 온다(실측 2026-09-04: 1,325자 →
# 295개). 그 조각을 그대로 이벤트로 내보내면 `routes/turns.py`의 `_redacted`가
# 깨진다 — 그쪽은 이벤트마다 `redact_credentials`를 돌리는데, 패턴 다섯 개가 전체
# 문자열 매칭이라(`AKIA[0-9A-Z]{12,}` 등) `AKIA`와 뒷부분이 각각 매칭에 실패한다.
# 즉 **자격증명이 조용히 클라이언트로 나간다.** 블록 단위로 오던 동안 우연히
# 안전했던 것이고, 스트리밍은 그 우연을 없앤다.
#
# **왜 공백인가.** 그 패턴 다섯 개는 모두 공백 없는 토큰 안에서만 매칭된다
# (`\S+`, `[0-9A-Z]{12,}`, `[A-Za-z0-9\-]{10,}`, `[A-Za-z0-9\-]{4,}`). 매칭이 공백을
# 넘을 수 없으므로, **공백으로 끝나는 조각 안의 매칭은 항상 그 조각 안에 온전히
# 들어 있다.** 그래서 공백 없는 꼬리만 붙들면 레댁션의 원래 보장이 그대로 복원된다.
#
# **이 모듈은 자격증명을 모른다.** 아는 것은 "공백 없는 토큰을 쪼개지 않는다"는
# 불변식 하나뿐이고, 레댁션은 계속 라우트가 소유한다. 패턴이 늘어날 때 고쳐야 할
# 곳이 두 곳이 되지 않게 하려는 분리다 — 새 패턴도 공백 없는 토큰이면 여기는
# 그대로다. 만약 언젠가 공백을 포함하는 패턴이 생기면 그 전제가 깨지므로,
# redaction.py 쪽에 그 사실을 적어 두는 것이 이 모듈을 고치는 것보다 먼저다.
from __future__ import annotations

import re

#: 버퍼 끝의 공백 없는 토큰. 이 토큰이 시작하는 위치가 안전한 절단점이다 —
#: 그 앞은 공백으로 끝나므로 어떤 패턴도 경계를 넘어 자랄 수 없다.
#:
#: `$`가 아니라 `\Z`인 이유: `$`는 문자열 끝의 개행 **앞**에서도 참이라
#: `"abc\n"`에서 절단점을 0으로 계산해 완성된 줄을 붙들어 버린다.
_TRAILING_RUN = re.compile(r"\S*\Z")


class WhitespaceBoundaryBuffer:
    """델타를 받아, 공백으로 끝나는 부분만 돌려주고 꼬리는 붙든다.

    호출부 규약: `feed`의 반환이 빈 문자열이면 **이번 델타로 내보낼 것이 없다**는
    뜻이므로 이벤트를 만들지 않는다. 블록이 끝날 때(`content_block_stop`)
    `flush`로 꼬리를 받는다 — 그때가 토큰이 더 자라지 않는다고 확정되는 유일한
    지점이다. 중간에 flush하면 실제로는 이어지던 토큰을 쪼개어 이 불변식을
    스스로 깬다.
    """

    def __init__(self) -> None:
        self._held = ""

    def feed(self, chunk: str) -> str:
        """델타를 넣고, 지금 안전하게 내보낼 수 있는 텍스트를 돌려준다."""
        self._held += chunk
        cut = _TRAILING_RUN.search(self._held).start()  # type: ignore[union-attr]
        out, self._held = self._held[:cut], self._held[cut:]
        return out

    def flush(self) -> str:
        """붙들고 있던 꼬리를 모두 돌려주고 비운다. 없으면 빈 문자열."""
        out, self._held = self._held, ""
        return out
