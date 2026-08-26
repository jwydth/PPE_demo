export const CONFIRM_DELETE_INCIDENT =
  "Mark this incident as a false positive? This permanently deletes the record and cannot be undone.";

// Deleting every incident is confirmed by ConfirmDeleteAllDialog instead of a
// message string — it states the record count and requires the word to be
// typed, which a window.confirm() can't do.
