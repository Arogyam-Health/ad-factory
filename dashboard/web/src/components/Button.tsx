import type { ButtonHTMLAttributes, ReactNode } from "react";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger";
  children: ReactNode;
};

export function Button({ variant = "default", className = "", children, type = "button", ...props }: Props) {
  const kind = variant === "default" ? "" : ` btn-${variant}`;
  return (
    <button type={type} className={`btn${kind} ${className}`.trim()} {...props}>
      {children}
    </button>
  );
}
