import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Spinner } from "@/components/ui/loading"

const buttonVariants = cva(
  "sentra-button inline-flex items-center justify-center gap-1.5 rounded-md text-[13px] font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-45",
  {
    variants: {
      variant: {
        default: "button-primary bg-primary text-primary-foreground hover:bg-primary/90 h-8 px-3",
        outline: "button-outline border bg-card hover:bg-muted h-8 px-3",
        ghost: "button-ghost hover:bg-muted h-8 px-3",
      },
      size: { default: "", sm: "h-7 px-2.5 text-xs", lg: "h-10 px-4" },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
)

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  pending?: boolean
  tone?: "success" | "warning" | "danger"
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, pending, tone, disabled, children, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} disabled={disabled || pending} aria-busy={pending || undefined} data-tone={tone}>
    {pending && <Spinner />}{children}
  </button>
))
Button.displayName = "Button"
