// Extraction confidence and explanation review are separate signals.
export function pageNeedsReview(page, original = false) {
  if (original) return Boolean(page.needs_review || page.error);
  const explanation = page.detail || page.analysis;
  const review = explanation?.validation;
  const flagged = page.analysis_error || review?.status === 'needs_review' || explanation?.uncertainties?.length;
  return Boolean(flagged);
}
