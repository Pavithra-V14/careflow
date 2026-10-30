// Marks content the hospital (or its counsel) must supply. Medical, legal, consent and privacy wording
// is never written by the frontend: it renders through this component so it is visible and testable.
export function Placeholder({ children }: { children: string }) {
  return (
    <p className="placeholder" data-placeholder>
      {children}
    </p>
  );
}
