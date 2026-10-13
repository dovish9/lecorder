export async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const contentType = response.headers.get("content-type") || "";
  let data;
  if (contentType.includes("application/json")) {
    data = await response.json();
  } else {
    const text = (await response.text()).trim();
    const looksLikeHtml = /<\s*(?:!doctype|html|head|body|title|h1)\b/i.test(text);
    data = {error: looksLikeHtml ? "" : text};
  }
  if (!response.ok) {
    if (response.status === 405) {
      throw new Error("현재 실행 중인 앱에 새 기능이 적용되지 않았습니다. Lecorder를 다시 시작한 뒤 다시 시도해 주세요.");
    }
    throw new Error(data.error || `요청을 처리하지 못했습니다. (${response.status})`);
  }
  return data;
}
