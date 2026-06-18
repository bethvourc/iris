import Foundation
import IrisKit

/// Deterministic `ActivityFetching` for screenshots and UI tests, selected via
/// `--ui-test-home <scenario>`. It decodes canned JSON through the real
/// contract decoder, so each Home state is reproducible without a daemon.
struct ScriptedActivityFetcher: ActivityFetching {
    let scenario: String

    func activityFeed(
        days _: Int, limit _: Int, cursor _: String?, timezone _: String
    ) async throws -> ActivityFeed {
        switch scenario {
        case "loading":
            // Never resolves within a capture window: holds the skeleton.
            try await Task.sleep(for: .seconds(3600))
            return try Self.decode(Self.emptyJSON)
        case "error":
            throw IrisAPIError.daemonUnreachable(detail: "no daemon")
        case "empty":
            return try Self.decode(Self.emptyJSON)
        default:
            return try Self.decode(Self.populatedJSON)
        }
    }

    private static func decode(_ json: String) throws -> ActivityFeed {
        try IrisJSON.decoder().decode(ActivityFeed.self, from: Data(json.utf8))
    }

    private static let emptyJSON = """
    {"stats":{"sessions_this_week":0,"runs_completed":0,"runs_failed":0,
    "last_active_at":null},"days":[],"next_cursor":null}
    """

    private static let populatedJSON = """
    {
      "stats": {"sessions_this_week": 4, "runs_completed": 12, "runs_failed": 1,
                "last_active_at": "2026-06-14T13:20:00Z"},
      "days": [
        {"date": "2026-06-14", "label": "Today", "items": [
          {"id": "1", "kind": "voice_session", "time": "2026-06-14T13:20:00Z",
           "title": "Asked about today's calendar",
           "preview": "You have three things today…", "status": "done"},
          {"id": "2", "kind": "run", "time": "2026-06-14T11:05:00Z",
           "title": "Drafted a follow-up email", "preview": "Sent to Sam", "status": "done"},
          {"id": "3", "kind": "run", "time": "2026-06-14T09:40:00Z",
           "title": "Export weekly report", "preview": "Tool failed: timeout", "status": "failed"}
        ]},
        {"date": "2026-06-13", "label": "Yesterday", "items": [
          {"id": "4", "kind": "meeting", "time": "2026-06-13T15:00:00Z",
           "title": "Standup notes", "preview": "Captured 6 action items", "status": "done"},
          {"id": "5", "kind": "message", "time": "2026-06-13T10:12:00Z",
           "title": "Reminder set", "preview": "3 p.m. — call the dentist", "status": "done"}
        ]}
      ],
      "next_cursor": null
    }
    """
}
