// 질문 파일 마크다운을 **렌더 직전에만** 손본다. 파일 자체는 건드리지 않는다.
//
// **왜 필요한가(2026-08-16의 결함).** 백엔드가 제출된 답변을 질문 파일의
// `[Answer]:` 칸에 심는데(backend/aipds/agent/question_file_answers.py),
// 화면에는 답변이 하나도 나타나지 않았다. 파일에는 `[Answer]: A`가 들어 있었다.
//
// 원인은 CommonMark다. 줄 맨 앞의 `[Answer]: A`는 **링크 참조 정의**(link
// reference definition)이고, 렌더러는 그 줄에 대해 **아무 출력도 만들지 않는다.**
// 게다가 정의가 하나 존재하는 순간 문서 안의 다른 `[Answer]`가 모두 그 정의를
// 가리키는 **링크**로 바뀐다 — 실측 화면에서 보기 텍스트가
// `X) Other (please describe after Answer: tag below)`로, 대괄호가 사라지고
// "Answer"에 밑줄이 그어져 나온 것이 그 증거다.
//
// **왜 파일 형식을 바꾸지 않는가.** 상류 ai-plc 룰이 `[Answer]:` 태그를 읽으라고
// 지시하고(aws-aiplc-rule-details/common/question-format-guide.md) 우리 파서도 그
// 정규식이다(backend/aipds/parsers/questions.py). 파일을 바꾸면 그 호환성이
// 깨진다. 그래서 **표시 직전에만** 바꾼다.
//
// **왜 파서로 렌더하지 않는가.** 질문 파일에는 질문 외의 본문이 많다 — 필수 영역
// 표, 스테이지 메타, 후속 질문 절. `QuestionFile` 모델은 preamble + questions만
// 담으므로 그것으로 그리면 그 맥락이 전부 사라진다.

//: 줄 맨 앞의 `[Answer]:` 한 줄. `^`가 필수다 — 문장 중간의 `[Answer]:`는
//: 링크 정의가 아니라 그냥 텍스트이고, 건드리면 안내 문구가 굵게 변한다.
const ANSWER_LINE = /^\[Answer\]:[ \t]*(.*)$/gm;

/**
 * 마크다운을 렌더 직전에 손본다. `[Answer]: A` → `**\[Answer\]:** A`.
 *
 * 대괄호를 escape하는 것이 핵심이다: escape하지 않으면 여전히 링크 정의로
 * 읽혀서 같은 문제가 남는다. 굵게 감싸는 것은 답변이 문서에서 눈에 띄어야
 * 하기 때문이다 — 사용자가 "내가 답한 것이 기록됐나"를 확인하는 자리다.
 *
 * 빈 칸(`[Answer]:` 뒤에 아무것도 없음)도 그대로 보여준다. 아직 답하지 않은
 * 문항이라는 사실 자체가 정보이고, 숨기면 "답했는데 안 보인다"와 "아직 안
 * 물어봤다"를 구별할 수 없다.
 */
export function revealAnswerTags(markdown: string): string {
  return markdown.replace(ANSWER_LINE, (_line, value: string) => {
    const answer = value.trim();
    return answer === "" ? "**\\[Answer\\]:**" : `**\\[Answer\\]:** ${answer}`;
  });
}


//: 보기 한 줄. 백엔드 파서와 **같은 집합**이어야 한다
//: (backend/aipds/parsers/questions.py의 `_OPTION`: `^([A-F]|X)\)\s+`).
//: 한쪽만 넓히면 화면에는 보기로 보이는데 파싱은 안 되거나 그 반대가 된다.
const OPTION_LINE = /^([A-FX]\)[ \t]+.*?)[ \t]*$/gm;

/**
 * 보기 줄 끝에 마크다운 하드 브레이크(공백 2개)를 붙인다.
 *
 * **왜 필요한가.** 상류 형식은 보기를 줄바꿈으로만 구분하고 줄 끝에 공백을 두지
 * 않는다(question-format-guide.md). CommonMark에서 빈 줄 없이 이어진 줄은 한
 * 문단이고 그 줄바꿈은 **공백으로** 렌더되므로, `A) … B) … C) …`가 한 줄로 쭉
 * 이어져 읽을 수 없었다. `A)`는 목록 표지도 아니다 — 순서 목록은 숫자 + `.`/`)`
 * 만 인정하므로 letter 보기는 그냥 텍스트다.
 *
 * 불릿 목록(`- A) …`)으로 바꾸지 않는 이유: 들여쓰기와 불릿이 생겨 문서 모양이
 * 바뀐다. 하드 브레이크는 원래 의도한 "한 줄에 하나"를 그대로 만든다.
 *
 * 문단 마지막 줄에 붙는 하드 브레이크는 렌더러가 그냥 버리므로 무해하다.
 */
export function breakOptionLines(markdown: string): string {
  return markdown.replace(OPTION_LINE, "$1  ");
}

/**
 * 질문 파일을 화면에 올리기 전 변환 묶음. 순서는 무관하다(서로 다른 줄을 본다).
 */
export function prepareQuestionMarkdown(markdown: string): string {
  return breakOptionLines(revealAnswerTags(markdown));
}
