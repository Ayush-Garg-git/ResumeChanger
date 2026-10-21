// A small word-level diff, used to highlight what a suggestion changes.
//
// It finds the longest common subsequence (LCS) of words: the words both
// versions share, in order. Everything else is either removed (only in the
// original) or added (only in the proposal).
//
// Returns a list like:
//   [{ type: "same", text: "Built a" }, { type: "removed", text: "dashboard" },
//    { type: "added", text: "sales dashboard" }, ...]

export function diffWords(before, after) {
  const a = before.split(/(\s+)/).filter(Boolean); // keep spaces as tokens
  const b = after.split(/(\s+)/).filter(Boolean);

  // lcs[i][j] = length of the LCS of a[i:] and b[j:]
  const lcs = Array.from({ length: a.length + 1 }, () =>
    new Array(b.length + 1).fill(0)
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const parts = [];
  const push = (type, text) => {
    const last = parts[parts.length - 1];
    if (last && last.type === type) last.text += text;
    else parts.push({ type, text });
  };

  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      push("same", a[i]);
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      push("removed", a[i++]);
    } else {
      push("added", b[j++]);
    }
  }
  while (i < a.length) push("removed", a[i++]);
  while (j < b.length) push("added", b[j++]);
  return parts;
}
