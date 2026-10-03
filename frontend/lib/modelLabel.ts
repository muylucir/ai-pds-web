// frontend/lib/modelLabel.ts — 모델 표시 이름에 effort를 붙인다.
//
// 같은 모델이라도 effort에 따라 속도가 크게 다르다(Opus 5.5 medium → high: 턴 시간
// +53%). 그래서 모델을 보여주는 곳(생성 콤보박스, 헤더 배지, 프로젝트 목록)은 effort를
// 함께 보여준다. 한 함수로 두는 이유는 세 곳의 표기가 어긋나지 않게 하기 위해서다.
//
// effort가 없으면(null — CLI 기본값, 이 필드 이전의 프로젝트) 이름만 쓴다. 기본값이
// 무엇인지는 모델마다 다르고 프론트는 그것을 알 수 없으므로 지어내지 않는다.
import type { Dict } from "@/lib/i18n";

export function modelLabel(
  name: string, effort: string | null | undefined, t: (key: keyof Dict) => string,
): string {
  if (!effort) return name;
  return t("model.withEffort").replace("{name}", name).replace("{effort}", effort);
}
