// Minimal inline SVG icon set for the Main Live Dashboard header — replaces emoji
// glyphs (⌂ ⚠ ⏵) with vector icons sharing one stroke weight (1.6px) so recovery,
// alert, and replay states read as one consistent icon language rather than
// font-dependent characters that render differently per platform.

interface IconProps {
  className?: string;
}

export function HouseIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden="true">
      <path
        d="M2.5 7.5 8 3l5.5 4.5M4 6.5V13h8V6.5"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M6.5 13v-3.5h3V13" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export function WarningTriangleIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden="true">
      <path
        d="M8 2.5 14 13H2L8 2.5Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
      <path d="M8 6.5v3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="8" cy="11" r="0.9" fill="currentColor" />
    </svg>
  );
}

export function PlayIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden="true">
      <path d="M4.5 3.2v9.6l8-4.8-8-4.8Z" fill="currentColor" />
    </svg>
  );
}
