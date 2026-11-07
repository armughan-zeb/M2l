export function approvedSourceCredit(scene = {}) {
  const credit = scene.media?.source_credit || scene.primary_visual?.asset?.source_credit;
  if (credit?.approved !== true || !['primary_source_review', 'verified_media'].includes(credit.source)
    || !/^https:\/\//i.test(String(credit.source_url || ''))
    || typeof credit.label !== 'string' || !credit.label.trim() || credit.label.length > 96) return null;
  return credit;
}
