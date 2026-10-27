(() => {
  const want = "__TEXT__";
  const els = [...document.querySelectorAll('button,a,[role="button"],input[type="submit"]')];
  const el = els.find(e => ((e.innerText || e.value || "").trim().replace(/\s+/g, " ")) === want);
  if (!el) return "NOT_FOUND";
  const path = [];
  let n = el;
  while (n && n.nodeType === 1 && n !== document.documentElement) {
    let seg = n.tagName.toLowerCase();
    const parent = n.parentElement;
    if (parent) {
      const sibs = [...parent.children].filter(c => c.tagName === n.tagName);
      if (sibs.length > 1) seg += ':nth-of-type(' + (sibs.indexOf(n) + 1) + ')';
    }
    path.unshift(seg);
    n = parent;
  }
  const sel = "body > " + path.join(" > ");
  const hits = document.querySelectorAll(sel);
  return sel + "  [matches=" + hits.length + "]";
})()
