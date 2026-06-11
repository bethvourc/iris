import Foundation

/// Jittered exponential backoff shared by SSEClient and DaemonManager.
enum Backoff {
    static func duration(attempt: Int, minimum: Duration, maximum: Duration) -> Duration {
        let exponent = min(max(attempt - 1, 0), 8)
        let base = seconds(minimum) * pow(2, Double(exponent))
        let capped = min(base, seconds(maximum))
        return .seconds(capped * Double.random(in: 0.8 ... 1.2))
    }

    private static func seconds(_ duration: Duration) -> Double {
        Double(duration.components.seconds)
            + Double(duration.components.attoseconds) / 1e18
    }
}
