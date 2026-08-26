// frontend/lib/api/account.ts — /me* 클라이언트 (자기 계정).
//
// adminUsers.ts와 나누는 이유는 인가 모델이 다르기 때문이다: 그쪽은 admin이 남의
// 계정을 다루고, 여기는 누구든 자기 계정만 다룬다. 백엔드도 같은 이유로 라우터를
// 나눠 뒀다(backend/aipds/routes/account.py).
import { apiFetch } from "./http";

/**
 * 자기 비밀번호를 바꾼다. 성공은 204(본문 없음)다.
 *
 * 확인 입력(새 비밀번호 재입력)은 보내지 않는다 — 클라이언트에서만 쓰는 값이고,
 * 서버로 보내면 백엔드가 검사할 이유가 없는 필드를 하나 더 갖게 된다.
 *
 * 정책 검사도 하지 않는다: Cognito가 유일한 판정자이고, 두 벌의 정책은 반드시
 * 어긋나며 어긋난 쪽이 사용자에게 거짓말을 한다.
 */
export async function changePassword(currentPassword: string,
                                     newPassword: string): Promise<void> {
  await apiFetch<null>("/me/password", {
    method: "POST",
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
}
