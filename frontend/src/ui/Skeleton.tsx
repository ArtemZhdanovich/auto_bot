/** Skeleton-заглушки вместо Spinner (UI/UX ТЗ §1.5, §2.10; 02 §7.7).
 *  Геометрия — как у реальной карточки ленты (.lcard, редизайн §7.3):
 *  фото 16:9 + строки тела (модель 16 → мета 12 → цена 18 → спеки 12),
 *  ноль layout-shift при подмене на данные. Фон карточки — --surface,
 *  чтобы скелетон читался и в тёмной теме. */
export function SkeletonCard() {
  return (
    <div className="lcard lcard--skeleton" aria-hidden="true">
      <div className="lcard__media">
        <span className="skeleton lcard__img-skel" />
      </div>
      <div className="lcard__body">
        <span className="skeleton" style={{ height: 16, width: "68%" }} />
        <span className="skeleton" style={{ height: 12, width: "44%" }} />
        <span className="skeleton" style={{ height: 18, width: "38%" }} />
        <span className="skeleton" style={{ height: 12, width: "62%" }} />
      </div>
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
