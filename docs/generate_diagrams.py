"""
Generate architecture diagram PNGs for the Basketball Game Scheduler.
Run: python3 docs/generate_diagrams.py
Outputs PNGs into docs/diagrams/
"""

import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "diagrams")
os.makedirs(OUTPUT_DIR, exist_ok=True)

from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import Lambda
from diagrams.aws.database import Dynamodb
from diagrams.aws.storage import S3
from diagrams.aws.integration import Eventbridge
from diagrams.aws.engagement import SES, SimpleEmailServiceSesEmail
from diagrams.aws.ml import Bedrock
from diagrams.aws.network import Route53
from diagrams.onprem.client import User


# ──────────────────────────────────────────────
# Diagram 1 — Component Architecture Overview
# ──────────────────────────────────────────────
def diagram_1_component_architecture():
    with Diagram(
        "Basketball Scheduler — Component Architecture",
        filename=os.path.join(OUTPUT_DIR, "01_component_architecture"),
        show=False,
        direction="TB",
        graph_attr={"fontsize": "14", "bgcolor": "white", "pad": "0.5"},
    ):
        player = User("100 Players")
        domain = Route53("Route 53\nDomain + MX")

        with Cluster("AWS eu-west-1"):
            with Cluster("Scheduling"):
                eb_mon = Eventbridge("EventBridge\nMonday 9AM")
                eb_wf = Eventbridge("EventBridge\nWed & Fri 9AM")
                eb_sat = Eventbridge("EventBridge\nSaturday 1PM UTC")

            with Cluster("Compute"):
                fn_announce = Lambda("announcement\n-sender")
                fn_email = Lambda("email\n-processor")
                fn_remind = Lambda("reminder\n-checker")
                fn_finalize = Lambda("game\n-finalizer")
                fn_admin = Lambda("admin\n-processor")

            with Cluster("Storage"):
                dynamo = Dynamodb("DynamoDB\nPlayers / Games / RSVPs")
                s3 = S3("S3\nInbound Emails")

            with Cluster("Email (SES)"):
                ses_out = SES("SES Outbound")
                ses_in = SimpleEmailServiceSesEmail("SES Inbound\nRule Set")

            bedrock = Bedrock("Bedrock\nClaude Haiku 3")

        admin = User("Admin")

        # Scheduling triggers
        eb_mon >> Edge(label="trigger") >> fn_announce
        eb_wf >> Edge(label="trigger") >> fn_remind
        eb_sat >> Edge(label="trigger") >> fn_finalize

        # Announcement sender
        fn_announce >> Edge(label="create game\n+ read players") >> dynamo
        fn_announce >> Edge(label="send emails") >> ses_out

        # Reminder checker
        fn_remind >> Edge(label="check RSVPs") >> dynamo
        fn_remind >> Edge(label="send reminders") >> ses_out

        # Game finalizer
        fn_finalize >> Edge(label="OPEN → PLAYED") >> dynamo

        # Inbound email flow
        domain >> Edge(label="MX") >> ses_in
        player >> Edge(label="replies") >> ses_in
        ses_in >> Edge(label="store\ninbound/") >> s3
        s3 >> Edge(label="S3 event") >> fn_email
        fn_email >> Edge(label="parse intent") >> bedrock
        fn_email >> Edge(label="update RSVP") >> dynamo
        fn_email >> Edge(label="send reply") >> ses_out

        # Admin email flow
        admin >> Edge(label="admin commands", color="purple") >> ses_in
        ses_in >> Edge(label="store\nadmin/", color="purple") >> s3
        s3 >> Edge(label="S3 event", color="purple") >> fn_admin
        fn_admin >> Edge(label="parse command", color="purple") >> bedrock
        fn_admin >> Edge(label="cancel/manage\nplayers", color="purple") >> dynamo
        fn_admin >> Edge(label="send reply", color="purple") >> ses_out

        ses_out >> Edge(label="emails") >> player
        ses_out >> Edge(label="confirmations", color="purple") >> admin


# ──────────────────────────────────────────────
# Diagram 2 — Monday Announcement Flow
# ──────────────────────────────────────────────
def diagram_2_announcement_flow():
    with Diagram(
        "Monday Announcement Flow",
        filename=os.path.join(OUTPUT_DIR, "02_announcement_flow"),
        show=False,
        direction="LR",
        graph_attr={"fontsize": "14", "bgcolor": "white", "pad": "0.5"},
    ):
        eb = Eventbridge("EventBridge\nMonday 9AM")
        fn = Lambda("announcement\n-sender")
        dynamo = Dynamodb("DynamoDB")
        ses = SES("SES Outbound")
        players = User("100 Players")

        eb >> Edge(label="1. trigger") >> fn
        fn >> Edge(label="2. createGame()\n+ getPlayers()") >> dynamo
        fn >> Edge(label="3. sendEmail()\nx 100") >> ses
        ses >> Edge(label="4. deliver") >> players
        fn >> Edge(label="5. setRSVP(PENDING)\nx 100") >> dynamo


# ──────────────────────────────────────────────
# Diagram 3 — Player Reply / NLU Processing
# ──────────────────────────────────────────────
def diagram_3_email_processing():
    with Diagram(
        "Player Reply Processing (NLU)",
        filename=os.path.join(OUTPUT_DIR, "03_email_processing"),
        show=False,
        direction="LR",
        graph_attr={"fontsize": "14", "bgcolor": "white", "pad": "0.5"},
    ):
        player = User("Player")
        ses_in = SimpleEmailServiceSesEmail("SES Inbound")
        s3 = S3("S3\nRaw Email")
        fn = Lambda("email\n-processor")
        bedrock = Bedrock("Bedrock\nClaude Haiku 3")
        dynamo = Dynamodb("DynamoDB")
        ses_out = SES("SES Outbound")

        player >> Edge(label="1. reply email") >> ses_in
        ses_in >> Edge(label="2. store") >> s3
        s3 >> Edge(label="3. S3 event") >> fn
        fn >> Edge(label="4. getObject()") >> s3
        fn >> Edge(label="5. getRoster()") >> dynamo
        fn >> Edge(label="6. prompt(\nemail + roster)") >> bedrock
        bedrock >> Edge(label="7. intent +\nreply draft") >> fn
        fn >> Edge(label="8. updateRSVP()") >> dynamo
        fn >> Edge(label="9. send reply") >> ses_out
        ses_out >> Edge(label="10. deliver") >> player


# ──────────────────────────────────────────────
# Diagram 4 — Reminder & Cancellation Flow
# ──────────────────────────────────────────────
def diagram_4_reminder_flow():
    with Diagram(
        "Reminder and Cancellation Flow",
        filename=os.path.join(OUTPUT_DIR, "04_reminder_flow"),
        show=False,
        direction="LR",
        graph_attr={"fontsize": "14", "bgcolor": "white", "pad": "0.5"},
    ):
        eb = Eventbridge("EventBridge\nWed & Fri 9AM")
        fn = Lambda("reminder\n-checker")
        dynamo = Dynamodb("DynamoDB")
        ses = SES("SES Outbound")
        pending = User("Pending\nPlayers")
        all_players = User("All Players")

        eb >> Edge(label="1. trigger") >> fn
        fn >> Edge(label="2. getConfirmedCount()\n+ getPendingPlayers()") >> dynamo

        fn >> Edge(
            label="3a. if < 6 confirmed\nsend reminder",
            style="dashed",
            color="orange",
        ) >> ses
        ses >> Edge(label="remind", color="orange") >> pending

        fn >> Edge(
            label="3b. if Friday & still < 6\nsend cancellation",
            style="dashed",
            color="red",
        ) >> ses
        ses >> Edge(label="cancel", color="red") >> all_players

        fn >> Edge(
            label="3c. if >= 6\nno action needed",
            style="dotted",
            color="green",
        ) >> dynamo


# ──────────────────────────────────────────────
# Diagram 5 — Game Finalisation Flow
# ──────────────────────────────────────────────
def diagram_5_game_finalizer_flow():
    with Diagram(
        "Game Finalisation Flow",
        filename=os.path.join(OUTPUT_DIR, "05_game_finalizer_flow"),
        show=False,
        direction="LR",
        graph_attr={"fontsize": "14", "bgcolor": "white", "pad": "0.5"},
    ):
        eb = Eventbridge("EventBridge\nSaturday 1PM UTC")
        fn = Lambda("game\n-finalizer")
        dynamo = Dynamodb("DynamoDB")

        eb >> Edge(label="1. trigger") >> fn
        fn >> Edge(label="2. getGameStatus(today)") >> dynamo
        fn >> Edge(
            label="3. if OPEN → PLAYED",
            color="green",
        ) >> dynamo
        fn >> Edge(
            label="3. if CANCELLED/PLAYED\n→ no-op",
            style="dashed",
            color="gray",
        ) >> dynamo


# ──────────────────────────────────────────────
# Diagram 6 — Data Model
# ──────────────────────────────────────────────
def diagram_6_data_model():
    """Uses graphviz directly to draw the two-table DynamoDB data model."""
    import graphviz

    dot = graphviz.Digraph("DataModel", format="png")
    dot.attr(rankdir="TB", bgcolor="white", pad="0.5", dpi="150",
             label="DynamoDB Data Model — Single-Table Games Design",
             labelloc="t", fontsize="20", fontname="Helvetica")
    dot.attr("node", shape="none", fontname="Helvetica")
    dot.attr("edge", fontname="Helvetica", fontsize="11")

    # PLAYERS table
    dot.node("players", '''<
        <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" BGCOLOR="white" COLOR="#2171b5">
            <TR><TD BGCOLOR="#2171b5" COLSPAN="3"><FONT COLOR="white"><B>Table: Players</B></FONT></TD></TR>
            <TR><TD BGCOLOR="#e8f4fd" COLSPAN="3"><B>Player profiles — email as PK, active status as SK</B></TD></TR>
            <TR><TD BGCOLOR="#ddd" ALIGN="LEFT"><B>Attribute</B></TD><TD BGCOLOR="#ddd" ALIGN="LEFT"><B>Key</B></TD><TD BGCOLOR="#ddd" ALIGN="LEFT"><B>Type</B></TD></TR>
            <TR><TD ALIGN="LEFT"><FONT COLOR="#2171b5"><B>email</B></FONT></TD><TD ALIGN="LEFT"><I>PK</I></TD><TD ALIGN="LEFT">string</TD></TR>
            <TR><TD ALIGN="LEFT"><FONT COLOR="#2171b5"><B>active</B></FONT></TD><TD ALIGN="LEFT"><I>SK</I></TD><TD ALIGN="LEFT">string ("true" / "false" / "guest#active" / "guest#active#&lt;name&gt;")</TD></TR>
            <TR><TD ALIGN="LEFT">name</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">string (nullable)</TD></TR>
            <TR><TD ALIGN="LEFT">isAdmin</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">boolean (admin players only)</TD></TR>
            <TR><TD ALIGN="LEFT">sponsorEmail</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">string (guest entries only)</TD></TR>
            <TR><TD ALIGN="LEFT">gameDate</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">string (guest entries only)</TD></TR>
        </TABLE>
    >''')

    # GAMES table
    dot.node("games", '''<
        <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" BGCOLOR="white" COLOR="#2171b5">
            <TR><TD BGCOLOR="#2171b5" COLSPAN="3"><FONT COLOR="white"><B>Table: Games</B></FONT></TD></TR>
            <TR><TD BGCOLOR="#e8f4fd" COLSPAN="3"><B>PK = game date, SK = gameStatus or playerStatus#YES/NO/MAYBE</B></TD></TR>
            <TR><TD BGCOLOR="#ddd" ALIGN="LEFT"><B>Attribute</B></TD><TD BGCOLOR="#ddd" ALIGN="LEFT"><B>Key</B></TD><TD BGCOLOR="#ddd" ALIGN="LEFT"><B>Type</B></TD></TR>
            <TR><TD ALIGN="LEFT"><FONT COLOR="#2171b5"><B>gameDate</B></FONT></TD><TD ALIGN="LEFT"><I>PK</I></TD><TD ALIGN="LEFT">string (YYYY-MM-DD)</TD></TR>
            <TR><TD ALIGN="LEFT"><FONT COLOR="#2171b5"><B>sk</B></FONT></TD><TD ALIGN="LEFT"><I>SK</I></TD><TD ALIGN="LEFT">gameStatus | playerStatus#YES/NO/MAYBE</TD></TR>
            <TR><TD ALIGN="LEFT">players</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">Map (see below)</TD></TR>
            <TR><TD ALIGN="LEFT">status</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">OPEN | CANCELLED | PLAYED<BR/>(only on SK=gameStatus)</TD></TR>
            <TR><TD ALIGN="LEFT">createdAt</TD><TD ALIGN="LEFT"></TD><TD ALIGN="LEFT">timestamp<BR/>(only on SK=gameStatus)</TD></TR>
        </TABLE>
    >''')

    # Example items
    dot.node("examples", '''<
        <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" CELLPADDING="6" BGCOLOR="#fffbe6" COLOR="#b58900">
            <TR><TD BGCOLOR="#b58900" COLSPAN="4"><FONT COLOR="white"><B>Example Items in Games Table</B></FONT></TD></TR>
            <TR><TD BGCOLOR="#f5f0d0" ALIGN="LEFT"><B>PK</B></TD><TD BGCOLOR="#f5f0d0" ALIGN="LEFT"><B>SK</B></TD><TD BGCOLOR="#f5f0d0" ALIGN="LEFT"><B>players</B></TD><TD BGCOLOR="#f5f0d0" ALIGN="LEFT"><B>Other</B></TD></TR>
            <TR><TD ALIGN="LEFT">2026-03-28</TD><TD ALIGN="LEFT">gameStatus</TD><TD ALIGN="LEFT">—</TD><TD ALIGN="LEFT">status=OPEN, createdAt=...</TD></TR>
            <TR><TD ALIGN="LEFT">2026-03-28</TD><TD ALIGN="LEFT">playerStatus#YES</TD><TD ALIGN="LEFT">{"john@mail.com": {"name": "John"},<BR/> "jane@mail.com": {"name": "Jane"}}</TD><TD ALIGN="LEFT">guests: [{pk,sk,name,sponsorEmail,sponsorName}]</TD></TR>
            <TR><TD ALIGN="LEFT">2026-03-28</TD><TD ALIGN="LEFT">playerStatus#NO</TD><TD ALIGN="LEFT">{"bob@mail.com": {"name": "Bob"}}</TD><TD ALIGN="LEFT">guests: []</TD></TR>
            <TR><TD ALIGN="LEFT">2026-03-28</TD><TD ALIGN="LEFT">playerStatus#MAYBE</TD><TD ALIGN="LEFT">{"alice@mail.com": {"name": "Alice"}}</TD><TD ALIGN="LEFT">guests: []</TD></TR>
        </TABLE>
    >''')

    # Layout edges
    dot.edge("players", "games", label="email referenced\nin players map", color="#6baed6",
             penwidth="2", style="dashed")
    dot.edge("games", "examples", label="", style="dotted", color="#b58900", arrowhead="none")

    output_path = os.path.join(OUTPUT_DIR, "06_data_model")
    dot.render(output_path, cleanup=True)


# ──────────────────────────────────────────────
# Diagram 7 — Admin Command Flow
# ──────────────────────────────────────────────
def diagram_7_admin_flow():
    with Diagram(
        "Admin Command Flow",
        filename=os.path.join(OUTPUT_DIR, "07_admin_flow"),
        show=False,
        direction="LR",
        graph_attr={"fontsize": "14", "bgcolor": "white", "pad": "0.5"},
    ):
        admin = User("Admin")
        ses_in = SimpleEmailServiceSesEmail("SES Inbound\n(admin@ address)")
        s3 = S3("S3\nadmin/ prefix")
        fn = Lambda("admin\n-processor")
        bedrock = Bedrock("Bedrock\nClaude Haiku 3")
        dynamo = Dynamodb("DynamoDB")
        ses_out = SES("SES Outbound")

        admin >> Edge(label="1. email command\n(cancel, add, deactivate…)") >> ses_in
        ses_in >> Edge(label="2. store") >> s3
        s3 >> Edge(label="3. S3 event") >> fn
        fn >> Edge(label="4. is_admin() check") >> dynamo
        fn >> Edge(label="5. parse_admin_email()") >> bedrock
        bedrock >> Edge(label="6. intent + fields") >> fn
        fn >> Edge(label="7. cancel / add /\ndeactivate / reactivate") >> dynamo
        fn >> Edge(label="8. send confirmation") >> ses_out
        ses_out >> Edge(label="9. deliver") >> admin


if __name__ == "__main__":
    print("Generating diagram 1/7 — Component Architecture...")
    diagram_1_component_architecture()
    print("Generating diagram 2/7 — Monday Announcement Flow...")
    diagram_2_announcement_flow()
    print("Generating diagram 3/7 — Player Reply Processing...")
    diagram_3_email_processing()
    print("Generating diagram 4/7 — Reminder & Cancellation Flow...")
    diagram_4_reminder_flow()
    print("Generating diagram 5/7 — Game Finalisation Flow...")
    diagram_5_game_finalizer_flow()
    print("Generating diagram 6/7 — Data Model...")
    diagram_6_data_model()
    print("Generating diagram 7/7 — Admin Command Flow...")
    diagram_7_admin_flow()
    print(f"\nDone! All PNGs saved to {OUTPUT_DIR}/")
