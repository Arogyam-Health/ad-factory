export const reducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export const pageEase = [0.2, 0.7, 0.2, 1] as const;

export const fadeUp = {
  hidden: { opacity: 0, y: reducedMotion() ? 0 : 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.38, ease: pageEase } },
};

export const stagger = {
  hidden: {},
  show: {
    transition: {
      staggerChildren: reducedMotion() ? 0 : 0.05,
      delayChildren: reducedMotion() ? 0 : 0.04,
    },
  },
};
