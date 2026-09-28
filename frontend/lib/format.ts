export const pct = (p: number | null | undefined, digits = 0) =>
  p === null || p === undefined ? "–" : `${(p * 100).toFixed(digits)}%`;

export const kickoff = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }) + " UTC";

// Labels for stats.SET_STATS keys, in display order.
export const SET_STAT_LABELS: [string, string][] = [
  ["sweep", "Match ends 3-0 (either way)"],
  ["win_after_set1_win", "Wins match after winning set 1"],
  ["win_after_set1_loss", "Wins match after losing set 1"],
  ["win_after_1_1_from_1_0", "Wins match after 1-0 -> 1-1"],
  ["set3_win_when_up_2_0", "Wins set 3 when up 2-0"],
  ["set5_win", "Wins set 5 (at 2-2)"],
  ["split", "Split (sets 1 and 2 go 1-1)"],
  ["split_after_losing_set1", "Lost set 1, won set 2"],
  ["split_allowed_after_winning_set1", "Won set 1, lost set 2"],
];
