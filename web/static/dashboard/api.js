let datasetId=null;
export function bindDataset(id) {
  if(datasetId&&datasetId!==id)throw new Error('데이터 환경이 변경됐습니다. 페이지를 새로고침하세요.');
  datasetId=id;
}
export async function requestJson(url, options = {}) {
  const headers=new Headers(options.headers);
  if(datasetId && !['GET','HEAD'].includes((options.method||'GET').toUpperCase()))headers.set('X-Lecorder-Dataset',datasetId);
  const response = await fetch(url, {...options,headers});
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
    const error=new Error(data.error || `요청을 처리하지 못했습니다. (${response.status})`);
    error.code=data.code;
    throw error;
  }
  return data;
}
