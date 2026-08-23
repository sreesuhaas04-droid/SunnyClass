---
name: Obsidian Crimson Glass
colors:
  surface: '#121414'
  surface-dim: '#121414'
  surface-bright: '#38393a'
  surface-container-lowest: '#0d0e0f'
  surface-container-low: '#1a1c1c'
  surface-container: '#1e2020'
  surface-container-high: '#282a2b'
  surface-container-highest: '#333535'
  on-surface: '#e2e2e2'
  on-surface-variant: '#e9bcb5'
  inverse-surface: '#e2e2e2'
  inverse-on-surface: '#2f3131'
  outline: '#af8781'
  outline-variant: '#5e3f3a'
  surface-tint: '#ffb4a8'
  primary: '#ffb4a8'
  on-primary: '#680100'
  primary-container: '#dd0200'
  on-primary-container: '#ffedea'
  inverse-primary: '#c00200'
  secondary: '#ffb4ab'
  on-secondary: '#5e1612'
  secondary-container: '#7c2d26'
  on-secondary-container: '#ff9a8e'
  tertiary: '#b3c5ff'
  on-tertiary: '#002a77'
  tertiary-container: '#0061fa'
  on-tertiary-container: '#eff0ff'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#ffdad4'
  primary-fixed-dim: '#ffb4a8'
  on-primary-fixed: '#410000'
  on-primary-fixed-variant: '#930100'
  secondary-fixed: '#ffdad6'
  secondary-fixed-dim: '#ffb4ab'
  on-secondary-fixed: '#400203'
  on-secondary-fixed-variant: '#7c2d26'
  tertiary-fixed: '#dbe1ff'
  tertiary-fixed-dim: '#b3c5ff'
  on-tertiary-fixed: '#00174a'
  on-tertiary-fixed-variant: '#003ea7'
  background: '#121414'
  on-background: '#e2e2e2'
  surface-variant: '#333535'
  background-base: '#1A0706'
  glass-border: rgba(217, 217, 217, 0.15)
  glow-accent: rgba(221, 2, 0, 0.3)
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 64px
    fontWeight: '700'
    lineHeight: 72px
    letterSpacing: -0.04em
  display-lg-mobile:
    fontFamily: Inter
    fontSize: 40px
    fontWeight: '700'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-xl:
    fontFamily: Inter
    fontSize: 40px
    fontWeight: '600'
    lineHeight: 48px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.02em
  body-lg:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
    letterSpacing: 0em
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0.01em
  label-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.04em
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.06em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  unit: 8px
  gutter-desktop: 24px
  margin-desktop: 48px
  gutter-tablet: 16px
  margin-mobile: 20px
---

## Brand & Style

This design system is a sophisticated evolution of the glassmorphism aesthetic, transitioning into a darker, more atmospheric territory. It evokes the feeling of high-end automotive interfaces and precision-engineered luxury tech. The brand personality is powerful, mysterious, and focused, targeting professional users who appreciate a high-fidelity, "dark-room" environment.

The design style is **Glassmorphism mixed with High-Contrast Bold**. It relies on deep, dark depths—symbolizing "the void"—contrasted against razor-sharp highlights and liquid-like surfaces. The emotional response is one of intense focus and premium quality, where the UI doesn't just sit on the screen but feels physically layered like polished obsidian and heated glass.

## Colors

The palette is anchored in a monochromatic spectrum of blood-red and deep charcoal, creating a high-drama environment optimized for OLED displays.

*   **Primary (Racing Red):** The engine of the UI. Use `#DD0200` exclusively for critical calls to action, active selection states, and interactive highlights.
*   **Secondary (Black Cherry):** Used for depth. `#55100D` acts as the mid-layer for gradients and container backgrounds, providing a warmer, "heated" transition from the black background to the red accents.
*   **Neutral (Alabaster Grey):** Used for all high-contrast typography and iconography. At lower opacities (10-20%), it serves as the "specular highlight" for glass borders.
*   **Background (Coffee Bean):** The foundation. `#1A0706` is the near-black base that allows glass layers and red glows to pop with maximum luminosity.

## Typography

This system uses **Inter** for its systematic, neutral character, allowing the complex glass textures and vibrant colors to take center stage without typographic competition.

Headlines should be set with tight tracking to feel like solid blocks of Alabaster Grey. For body text, a slight increase in letter-spacing and line-height is required to maintain legibility against the heavy `40px` background blurs. Use **Medium (500)** or **SemiBold (600)** weights for labels to ensure they don't get lost in the "liquid" texture of the containers. All text defaults to Alabaster Grey, with secondary information scaled down to 60% opacity.

## Layout & Spacing

The layout utilizes a **12-column fluid grid** for desktop and an **8px hard grid** for internal component alignment.

- **Desktop:** 12 columns, 24px gutters. Use floating glass containers that do not touch the screen edges, creating a "suspended" effect.
- **Mobile:** 4 columns, 16px gutters, 20px outer margins.
- **Philosophy:** Spacing is "Airy & Suspended." Use generous internal padding (minimum 24px) within glass cards to prevent content from crowding the high-radius corners. Large "void" spaces of Coffee Bean (#1A0706) should be used to separate distinct functional areas.

## Elevation & Depth

Depth is achieved through **chromatic refraction** and **layered blurs** rather than traditional drop shadows.

1.  **Floor:** Solid Coffee Bean (`#1A0706`).
2.  **Level 1 (In-set Containers):** Black Cherry (`#55100D`) at 20% opacity, 20px blur, with a 1px Alabaster Grey stroke at 10% opacity.
3.  **Level 2 (Standard Cards):** Black Cherry (`#55100D`) at 40% opacity, 40px backdrop-blur. 1px stroke at 15% opacity. Added inner-glow of Racing Red (`#DD0200`) at 5% opacity.
4.  **Level 3 (Interactive/Modals):** 60px backdrop-blur. High-contrast 1px Alabaster stroke at 25%. A diffused outer glow of Racing Red (20% opacity, 40px spread) replaces standard shadows.

Every glass edge must feature a "specular highlight"—a 1px line that is slightly brighter on the top and left to simulate a light source catching the edge of the glass.

## Shapes

The shape language is "Liquid-Geometric." It uses significant corner radii to simulate the surface tension of a liquid droplet.

- **Small Components:** 8px - 12px for buttons and inputs.
- **Main Containers:** 24px - 32px for cards and floating panels to reinforce the "liquid glass" metaphor.
- **Active Indicators:** 4px radius or full-pill for small status indicators to differentiate them from structural containers.

## Components

- **Racing Red Buttons:** Solid `#DD0200` with Alabaster Grey text. Use a subtle inner-shadow (top-down) to create a "pressed" look.
- **Glass Inputs:** Fully encapsulated containers with a 40px backdrop blur. On focus, the 1px border transitions from Alabaster Grey (15%) to solid Racing Red with a 10px outer glow.
- **Liquid Cards:** The primary container. Features a linear gradient from top-left (Black Cherry at 40%) to bottom-right (Coffee Bean at 80%) plus the 40px backdrop-filter.
- **Pill Chips:** Use Level 1 elevation (20px blur). When active, they fill with a Black Cherry to Racing Red gradient.
- **Navigation Bars:** Floating glass modules, detached from the screen edge by 16px, using Level 3 elevation for maximum clear-space over content.
- **Scrollbars:** Ultra-thin (4px), Racing Red, with a 100% border radius, appearing only on hover to maintain the minimalist aesthetic.