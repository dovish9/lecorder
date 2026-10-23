export const node = (tag, text, className = '') => Object.assign(document.createElement(tag), {textContent:text, className});
export const duration = seconds => {
  if(seconds==null)return '미제공';
  const value=Math.round(seconds);
  if(value>=3600)return `${Math.floor(value/3600)}시간 ${Math.floor(value%3600/60)}분`;
  return value<60?`${value}초`:`${Math.floor(value/60)}분 ${value%60}초`;
};
export const number = value => value == null ? '미제공' : Number(value).toLocaleString('ko-KR');

export function picker(parent, label, options, selected, change) {
    const detail = node('details', '', 'insight-picker');
    const summary = node('summary', '');
    summary.append(node('span', options.find(([value]) => value === selected)?.[1] || label, 'insight-picker-label'));
    summary.setAttribute('aria-label', label);
    const list = node('div', '', 'insight-options');
    for (const [value, title] of options) {
      const button = node('button', title); button.type = 'button';
      button.setAttribute('aria-pressed', String(value === selected));
      button.addEventListener('click', () => {detail.open = false; change(value);});
      list.append(button);
    }
    detail.append(summary,list); parent.append(detail);
    detail.addEventListener('toggle',()=>{if(detail.open)parent.querySelectorAll('.insight-picker[open]').forEach(other=>{if(other!==detail)other.open=false;});});
    detail.addEventListener('keydown', event => {if (event.key === 'Escape') {event.stopPropagation();detail.open=false;summary.focus();}});
  }

export function setupMenus({listen}) {
  listen(document,'click',event=>{
    document.querySelectorAll('.insight-picker[open]').forEach(d=>{if(!d.contains(event.target))d.open=false;});
  });
}
