/** Skeleton-заглушки вместо Spinner (UI/UX ТЗ §1.5, §2.10). */

export function SkeletonCard() {
  return (
    <div className="listing-card">
      <div className="skeleton skeleton-card__photo" />
      <div className="skeleton skeleton-card__line" />
      <div className="skeleton skeleton-card__line skeleton-card__line--short" />
      <div style={{ height: 8 }} />
    </div>
  );
}

export function SkeletonList({ count = 4 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }, (_, i) => (
        <SkeletonCard key={i} />
      ))}
    </>
  );
}
