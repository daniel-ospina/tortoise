export const API = '';

export const C = {
  bg:'#0a0e14', text:'#c0caf5', muted:'#565f89',
  panel:'#131821', border:'#1a2030', accent:'#7aa2f7',
  surface:'#1a1f2e', mit:'#e0af68', nand:'#f7768e', impl:'#9ece6a',
};

export function wrapLines(ctx, text, maxChars, maxLines) {
  const lines=[]; let cur='';
  for (const w of (text||'').split(' ')) {
    const t=cur?cur+' '+w:w;
    if (t.length>maxChars&&cur){lines.push(cur);cur=w;}else cur=t;
  }
  if(cur)lines.push(cur);
  if(lines.length>maxLines)lines.length=maxLines;
  return lines;
}
