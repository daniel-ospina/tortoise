-- get_upcoming_events.applescript
-- Returns upcoming calendar events in the next 15 minutes WITH attendees
-- Output format: title ||| start_date ||| end_date ||| uid ||| ORGANIZER:name <email> ||| ATTENDEE:name <email> ...

tell application "Calendar"
	set eventList to {}
	repeat with cal in calendars
		try
			set theEvents to (events of cal whose start date >= (current date) and start date < (current date + 15 * minutes))
			repeat with ev in theEvents
				set eventLine to name of ev & "|||" & (start date of ev as text) & "|||" & (end date of ev as text) & "|||" & (uid of ev)
				
				-- Organizer
				try
					set orgName to name of organizer of ev
					set orgEmail to email of organizer of ev
					set eventLine to eventLine & "|||ORGANIZER:" & orgName & " <" & orgEmail & ">"
				end try
				
				-- Attendees
				try
					repeat with att in attendees of ev
						set attName to display name of att
						set attEmail to email of att
						set eventLine to eventLine & "|||ATTENDEE:" & attName & " <" & attEmail & ">"
					end repeat
				end try
				
				set end of eventList to eventLine
			end repeat
		end try
	end repeat
	return eventList as text
end tell
