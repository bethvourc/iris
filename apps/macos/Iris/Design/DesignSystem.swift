import AppKit
import SwiftUI

/// The single source of truth for the desktop console's visual language.
///
/// Centralizing tokens here is what keeps the no-AI-slop standard alive across
/// every later view: light surfaces, hairline borders, one royal-blue accent,
/// and moss/amber/rust reserved strictly for status. No gradients, no glows,
/// no decorative shapes live in this set. The desktop app deliberately owns
/// its own rules (sidebar layout) rather than inheriting the web dashboard's
/// top-nav language (docs/desktop/architecture.md).
enum DesignSystem {
    // MARK: - Color

    /// Semantic colors. Every token adapts to light/dark appearance so the
    /// shell reads correctly in both; callers never reach for system colors
    /// directly, so a palette change happens in exactly one place.
    enum Colors {
        /// The window canvas behind cards and content.
        static let canvas = dynamic(light: 0xF7_F7_F5, dark: 0x1C_1C_1E)
        /// Raised surface — cards, the selected sidebar row.
        static let surface = dynamic(light: 0xFF_FF_FF, dark: 0x25_25_27)
        /// A quieter fill for nested wells and the sidebar background.
        static let surfaceSecondary = dynamic(light: 0xF0_F0_ED, dark: 0x2D_2D_30)

        /// Hairline borders and dividers — never heavier than 1pt.
        static let border = dynamic(light: 0xE3_E3_DE, dark: 0x3A_3A_3D)

        static let textPrimary = dynamic(light: 0x1A_1A_18, dark: 0xF2_F2_F0)
        static let textSecondary = dynamic(light: 0x6B_6B_66, dark: 0xA0_A0_9B)
        static let textTertiary = dynamic(light: 0x9B_9B_95, dark: 0x6E_6E_69)

        /// The lone accent — a single royal blue. Brightened in dark mode to
        /// hold contrast against the dark canvas.
        static let accent = dynamic(light: 0x2D_54_CE, dark: 0x5C_7C_FF)

        /// Status hues, used only for status: healthy/success, caution,
        /// failure/destructive. Never decorative.
        static let moss = dynamic(light: 0x4E_7A_4F, dark: 0x74_A9_75)
        static let amber = dynamic(light: 0xB5_81_1F, dark: 0xE0_A5_3E)
        static let rust = dynamic(light: 0xB1_4A_2C, dark: 0xE0_7B_5B)
    }

    // MARK: - Typography

    /// The type scale. System font everywhere; data uses tabular, monospaced
    /// digits so numbers align and don't jitter as they update.
    enum Typography {
        static let title = Font.system(size: 22, weight: .semibold)
        static let heading = Font.system(size: 17, weight: .semibold)
        static let body = Font.system(size: 13)
        static let callout = Font.system(size: 13, weight: .medium)
        static let caption = Font.system(size: 11)

        /// Uppercase mono section headers (e.g. the Activity day dividers).
        static let sectionLabel = Font.system(size: 11, weight: .medium, design: .monospaced)

        /// Large numerals for stats — tabular so columns line up.
        static let dataLarge = Font.system(size: 28, weight: .semibold).monospacedDigit()
        /// Inline numerals inside running text or rows.
        static let data = Font.system(size: 13).monospacedDigit()
    }

    // MARK: - Spacing

    /// A 4pt-based spacing scale; using named steps keeps rhythm consistent.
    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12
        static let lg: CGFloat = 16
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
    }

    // MARK: - Radius

    /// Corner radii live in a tight 6–8pt band — soft, never pill-shaped.
    enum Radius {
        static let sm: CGFloat = 6
        static let md: CGFloat = 8
    }

    /// One hairline width for every border and divider.
    static let hairline: CGFloat = 1
}

private extension DesignSystem {
    /// Build an appearance-adaptive `Color` from light/dark sRGB hex values.
    static func dynamic(light: UInt32, dark: UInt32) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            let isDark = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
            return NSColor(hex: isDark ? dark : light)
        })
    }
}

private extension NSColor {
    convenience init(hex: UInt32) {
        self.init(
            srgbRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: 1
        )
    }
}
