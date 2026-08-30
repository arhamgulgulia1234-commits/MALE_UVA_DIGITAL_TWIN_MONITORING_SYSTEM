# Dashboard Page Overrides

> **PROJECT:** VAYUDRISHTI
> **Generated:** 2026-08-31 01:30:58
> **Page Type:** Dashboard / Data View

> ⚠️ **IMPORTANT:** Rules in this file **override** the Master file (`design-system/MASTER.md`).
> Only deviations from the Master are documented here. For all other rules, refer to the Master.

---

## Page-Specific Rules

### Layout Overrides

- **Max Width:** 1400px or full-width
- **Grid:** 12-column grid for data flexibility
- **Sections:** Hero (Video/Mission) > Solutions by Industry > Solutions by Role > Client Logos > Contact Sales

### Spacing Overrides

- **Content Density:** High — optimize for information display

### Typography Overrides

- No overrides — use Master typography

### Color Overrides

- **Strategy:** Corporate: Navy/Grey. High integrity. Conservative accents.

### Component Overrides

- Avoid: Auto-advance slides without a stop control
- Avoid: Depend on animationend or transitionend for required state correctness
- Avoid: Keyboard traps or illogical tab order

---

## Page-Specific Components

- No unique components for this page

---

## Recommendations

- Effects: Hover tooltips, chart zoom on click, row highlighting on hover, smooth filter animations, data loading spinners
- Animation: Provide previous next and play/pause; stop on focus or hover and when reduced motion is requested
- Animation: Cancel or replace prior motion; set the final semantic state directly and handle cancellation cleanup
- Accessibility: Keep tab order aligned with visual order and test every action without a pointer
- CTA Placement: Contact Sales (Primary) + Login (Secondary)
