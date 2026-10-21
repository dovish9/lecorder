/** Zoom a page without replacing its viewport or losing the chosen anchor. */
export function attachPageZoom(viewport, image, controls, initial, changed) {
  let zoom = initial;
  let gestureStart = null;
  let pointer = null;
  const clamp = value => Math.max(.5, Math.min(2.5, value));
  const baseWidth = () => {
    const style = getComputedStyle(viewport);
    return viewport.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  };
  function paint() {
    image.style.width = `${baseWidth() * zoom}px`;
    controls.label.textContent = `${Math.round(zoom * 100)}%`;
    controls.out.disabled = zoom <= .5;
    controls.in.disabled = zoom >= 2.5;
  }
  function setZoom(value, anchor) {
    const before = image.getBoundingClientRect();
    // Buttons keep the image's center in place; gestures keep the pointed content in place.
    const point = anchor || {x: before.left + before.width / 2, y: before.top + before.height / 2};
    const x = before.width ? (point.x - before.left) / before.width : .5;
    const y = before.height ? (point.y - before.top) / before.height : .5;
    zoom = clamp(value);
    paint();
    const after = image.getBoundingClientRect();
    viewport.scrollLeft += after.left + x * after.width - point.x;
    viewport.scrollTop += after.top + y * after.height - point.y;
    changed(zoom);
  }
  controls.out.onclick = () => setZoom(zoom - .25);
  controls.in.onclick = () => setZoom(zoom + .25);
  controls.label.onclick = () => setZoom(1);
  viewport.addEventListener('pointermove', e => { pointer = {x:e.clientX, y:e.clientY}; });
  viewport.addEventListener('wheel', e => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    if (gestureStart !== null) return;
    setZoom(zoom * Math.exp(-e.deltaY * .01), {x:e.clientX, y:e.clientY});
  }, {passive:false});
  // Safari emits GestureEvents for trackpad pinch instead of ctrl-wheel.
  viewport.addEventListener('gesturestart', e => { e.preventDefault(); gestureStart = zoom; }, {passive:false});
  viewport.addEventListener('gesturechange', e => {
    e.preventDefault();
    if (gestureStart === null) return;
    const anchor = e.clientX || e.clientY ? {x:e.clientX, y:e.clientY} : pointer;
    setZoom(gestureStart * e.scale, anchor);
  }, {passive:false});
  viewport.addEventListener('gestureend', e => { e.preventDefault(); gestureStart = null; }, {passive:false});
  // Defer initial sizing until the newly constructed workspace is attached.
  const frame = requestAnimationFrame(paint);
  const observer = new ResizeObserver(paint);
  observer.observe(viewport);
  image.addEventListener('load', paint);
  return () => { cancelAnimationFrame(frame); observer.disconnect(); };
}
