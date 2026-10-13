function checkMath(text) {
const issues = [];
const pattern = /\$\$([\s\S]*?)\$\$|(?<!\\)\$([^$\n]+)\$|\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)/g;
const remaining = text.replace(pattern, (token, display, inline, bracket, paren) => {
  try { katex.renderToString(display ?? inline ?? bracket ?? paren, {
    displayMode: display !== undefined || bracket !== undefined,
    throwOnError: true, strict: 'ignore', trust: false, maxExpand: 200, maxSize: 20,
  }); } catch (error) { issues.push(`수식 문법: ${token.slice(0, 160)} — ${error.message.slice(0, 180)}`); }
  return '';
});
if (/(?<!\\)\$|\\[\[\]()]/.test(remaining)) issues.push('닫히지 않았거나 줄바꿈으로 손상된 수식 구분자가 있습니다.');
return [...new Set(issues)].slice(0, 20);
}
