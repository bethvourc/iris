import Foundation
import IrisKit

/// Deterministic `ActivityServing` for screenshots and UI tests, selected via
/// `--ui-test-activity <scenario>`. It decodes canned JSON through the real
/// contract decoder, so each Activity state — including a two-page history and
/// a populated detail — is reproducible without a daemon.
struct ScriptedActivityService: ActivityServing {
    let scenario: String

    func activityFeed(
        days _: Int, limit _: Int, cursor: String?, timezone _: String
    ) async throws -> ActivityFeed {
        switch scenario {
        case "loading":
            try await Task.sleep(for: .seconds(3600))
            return try Self.decode(Self.emptyJSON, as: ActivityFeed.self)
        case "error":
            throw IrisAPIError.daemonUnreachable(detail: "no daemon")
        case "empty":
            return try Self.decode(Self.emptyJSON, as: ActivityFeed.self)
        default:
            let json = cursor == "p2" ? Self.page2JSON : Self.page1JSON
            return try Self.decode(json, as: ActivityFeed.self)
        }
    }

    func activityDetail(id: String) async throws -> ActivityDetail {
        try Self.decode(Self.detailJSON(for: id), as: ActivityDetail.self)
    }

    private static func decode<T: Decodable>(_ json: String, as _: T.Type) throws -> T {
        try IrisJSON.decoder().decode(T.self, from: Data(json.utf8))
    }

    private static let emptyJSON = """
    {"stats":{"sessions_this_week":0,"runs_completed":0,"runs_failed":0,
    "last_active_at":null},"days":[],"next_cursor":null}
    """

    private static let page1JSON = """
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
      "next_cursor": "p2"
    }
    """

    private static let page2JSON = """
    {
      "stats": {"sessions_this_week": 4, "runs_completed": 12, "runs_failed": 1,
                "last_active_at": "2026-06-14T13:20:00Z"},
      "days": [
        {"date": "2026-06-13", "label": "Yesterday", "items": [
          {"id": "6", "kind": "voice_session", "time": "2026-06-13T08:30:00Z",
           "title": "Morning briefing", "preview": "Weather and headlines", "status": "done"}
        ]},
        {"date": "2026-06-12", "label": "Thursday", "items": [
          {"id": "7", "kind": "run", "time": "2026-06-12T16:45:00Z",
           "title": "Booked a meeting room", "preview": "Room 4, 2 p.m.", "status": "done"}
        ]}
      ],
      "next_cursor": null
    }
    """

    private static func detailJSON(for id: String) -> String {
        """
        {
          "id": "\(id)", "kind": "voice_session", "time": "2026-06-14T13:20:00Z",
          "status": "done", "title": "Asked about today's calendar",
          "summary": "Walked through today's three calendar events.",
          "transcript": [
            {"role": "user", "text": "What's on my calendar today?",
             "time": "2026-06-14T13:20:02Z"},
            {"role": "assistant",
             "text": "You have three things today: a 10 a.m. design review, lunch with Sam, and a 3 p.m. one-on-one.",
             "time": "2026-06-14T13:20:05Z"},
            {"role": "user", "text": "Move the one-on-one to tomorrow.",
             "time": "2026-06-14T13:20:18Z"},
            {"role": "assistant",
             "text": "Done — I moved your one-on-one to tomorrow at 3 p.m. and notified the other attendee.",
             "time": "2026-06-14T13:20:21Z"}
          ],
          "run": null
        }
        """
    }
}
