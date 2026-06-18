import SwiftUI

/// Renders the current turn as readable conversation — the user's words and
/// Iris's reply, never raw JSON. Empty strings render nothing.
struct TranscriptView: View {
    let userText: String
    let assistantText: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !userText.isEmpty {
                Text(userText)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            if !assistantText.isEmpty {
                Text(assistantText)
                    .font(.system(size: 15))
                    .foregroundStyle(.primary)
                    .lineLimit(5)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .animation(.easeOut(duration: 0.12), value: assistantText)
    }
}
