import { motion } from "motion/react";
import type { ReactNode } from "react";
import { fadeUp, stagger } from "@/lib/motion";

type Span = "hero" | "side" | "wide" | "half" | "third";

type Props = {
  span?: Span;
  kicker?: string;
  title?: string;
  className?: string;
  children: ReactNode;
};

export function Tile({ span = "wide", kicker, title, className, children }: Props) {
  return (
    <motion.section className={`tile tile-${span}${className ? ` ${className}` : ""}`} variants={fadeUp}>
      <span className="tile-mark" aria-hidden="true" />
      {kicker ? <p className="tile-kicker">{kicker}</p> : null}
      {title ? <h2>{title}</h2> : null}
      {children}
    </motion.section>
  );
}

export function PressDrawer({
  title,
  wide,
  children,
}: {
  title: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <motion.section className={`press-drawer${wide ? " press-drawer-wide" : ""}`} variants={fadeUp}>
      <h3 className="press-drawer-title">{title}</h3>
      <div className="press-drawer-body press-cols">{children}</div>
    </motion.section>
  );
}

export function Bento({ children }: { children: ReactNode }) {
  return (
    <motion.div className="bento" variants={stagger} initial="hidden" animate="show">
      {children}
    </motion.div>
  );
}
