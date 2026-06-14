import SwiftUI

/// Shared building blocks rendered entirely from `DesignSystem` tokens. These
/// are the only primitives the section views (Home, Activity, Approvals,
/// Settings) should reach for when they need a card, a label, a status dot, or
/// an empty state — so consistency is structural, not a per-view discipline.

// MARK: - Card

/// A raised surface with a hairline border. The console's basic content unit.
struct Card<Content: View>: View {
    var padding: CGFloat = DesignSystem.Spacing.lg
    @ViewBuilder var content: () -> Content

    var body: some View {
        content()
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DesignSystem.Colors.surface)
            .clipShape(RoundedRectangle(cornerRadius: DesignSystem.Radius.md))
            .overlay(
                RoundedRectangle(cornerRadius: DesignSystem.Radius.md)
                    .strokeBorder(DesignSystem.Colors.border, lineWidth: DesignSystem.hairline)
            )
    }
}

// MARK: - Section label

/// An uppercase mono label that introduces a group of content.
struct SectionLabel: View {
    let text: String

    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text.uppercased())
            .font(DesignSystem.Typography.sectionLabel)
            .tracking(0.5)
            .foregroundStyle(DesignSystem.Colors.textTertiary)
            .accessibilityAddTraits(.isHeader)
    }
}

// MARK: - Status dot

/// A small filled dot that encodes state by color alone is not enough for
/// accessibility, so it always carries a label. `neutral` is the calm default;
/// the named cases map to the moss/amber/rust status hues.
struct StatusDot: View {
    enum Kind {
        case neutral
        case good
        case warning
        case bad

        var color: Color {
            switch self {
            case .neutral: DesignSystem.Colors.textTertiary
            case .good: DesignSystem.Colors.moss
            case .warning: DesignSystem.Colors.amber
            case .bad: DesignSystem.Colors.rust
            }
        }
    }

    let kind: Kind
    var diameter: CGFloat = 8

    var body: some View {
        Circle()
            .fill(kind.color)
            .frame(width: diameter, height: diameter)
    }
}

// MARK: - Empty state

/// The shared "nothing here yet" treatment: a dimmed brand mark, a title, a
/// short message, and an optional hint line (used to teach the hotkey).
struct EmptyStateView: View {
    let title: String
    let message: String
    var systemImage: String?
    var hint: String?

    var body: some View {
        VStack(spacing: DesignSystem.Spacing.md) {
            Group {
                if let systemImage {
                    Image(systemName: systemImage)
                        .font(.system(size: 32, weight: .light))
                } else {
                    SiriNewMark().frame(width: 40, height: 40)
                }
            }
            .foregroundStyle(DesignSystem.Colors.textTertiary)

            VStack(spacing: DesignSystem.Spacing.xs) {
                Text(title)
                    .font(DesignSystem.Typography.heading)
                    .foregroundStyle(DesignSystem.Colors.textPrimary)
                Text(message)
                    .font(DesignSystem.Typography.body)
                    .foregroundStyle(DesignSystem.Colors.textSecondary)
                    .multilineTextAlignment(.center)
            }

            if let hint {
                Text(hint)
                    .font(DesignSystem.Typography.callout)
                    .foregroundStyle(DesignSystem.Colors.textSecondary)
                    .padding(.vertical, DesignSystem.Spacing.xs)
                    .padding(.horizontal, DesignSystem.Spacing.md)
                    .background(DesignSystem.Colors.surfaceSecondary)
                    .clipShape(RoundedRectangle(cornerRadius: DesignSystem.Radius.sm))
            }
        }
        .frame(maxWidth: 320)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .combine)
    }
}
