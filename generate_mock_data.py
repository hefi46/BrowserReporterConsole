#!/usr/bin/env python3
"""
Mock Data Generator for Browser Reporter
Generates realistic browsing data for 540 users across 18 homegroups (1A-6C)
"""

import requests
import json
import random
import time
from datetime import datetime, timedelta
from typing import List, Dict

# Configuration
API_URL = "http://localhost:8000/api/reports/data"

# Homegroups and user distribution for load testing
HOMEGROUPS = ["1A", "1B", "1C", "2A", "2B", "2C", "3A", "3B", "3C", "4A", "4B", "4C", "5A", "5B", "5C", "6A", "6B", "6C"]
USERS_PER_GROUP = 30  # 18 groups * 30 users = 540 users

# Sample data for realistic browsing - expanded for load testing
FIRST_NAMES = [
    "John", "Jane", "Michael", "Sarah", "David", "Emma", "James", "Lisa",
    "Robert", "Emily", "William", "Ashley", "Christopher", "Jessica", "Daniel",
    "Amanda", "Matthew", "Stephanie", "Anthony", "Nicole", "Andrew", "Elizabeth",
    "Joshua", "Helen", "Kenneth", "Maria", "Paul", "Nancy", "Mark", "Betty",
    "Donald", "Dorothy", "Steven", "Sandra", "Brian", "Donna", "Edward", "Carol",
    "Ronald", "Ruth", "Timothy", "Sharon", "Jason", "Michelle", "Jeffrey", "Laura",
    "Ryan", "Kimberly", "Jacob", "Deborah", "Gary", "Amy", "Nicholas", "Angela",
    "Eric", "Brenda", "Jonathan", "Sophia", "Stephen", "Olivia", "Larry", "Cynthia",
    "Justin", "Marie", "Scott", "Janet", "Brandon", "Catherine", "Benjamin", "Frances",
    "Samuel", "Christine", "Gregory", "Samantha", "Frank", "Debra", "Raymond", "Rachel",
    "Alexander", "Carolyn", "Patrick", "Virginia", "Jack", "Isabella", "Dennis", "Heather",
    "Jerry", "Diane", "Tyler", "Julie", "Aaron", "Joyce", "Jose", "Victoria",
    "Henry", "Kelly", "Adam", "Christina", "Douglas", "Joan", "Nathan", "Evelyn",
    "Peter", "Lauren", "Zachary", "Judith", "Kyle", "Megan", "Arthur", "Cheryl",
    "Noah", "Andrea", "Carl", "Hannah", "Wayne", "Jacqueline", "Ralph", "Martha",
    "Roy", "Gloria", "Eugene", "Teresa", "Louis", "Sara", "Philip", "Janice",
    "Bobby", "Madison", "Johnny", "Julia", "Mason", "Kathryn", "Austin", "Abigail",
    "Ethan", "Alexis", "Kevin", "Natalie", "Christian", "Grace", "Elijah", "Chloe",
    "Dylan", "Alyssa", "Jordan", "Brianna", "Caleb", "Ella", "Lucas", "Lily",
    "Logan", "Hailey", "Owen", "Anna", "Isaac", "Zoe", "Carter", "Leah",
    "Connor", "Allison", "Landon", "Avery", "Wyatt", "Addison", "Hunter", "Audrey",
    "Cameron", "Maya", "Adrian", "Riley", "Evan", "Brooklyn", "Jaxon", "Savannah",
    "Gavin", "Claire", "Jeremiah", "Aubrey", "Colton", "Bella", "Dominic", "Violet",
    "Blake", "Skylar", "Ian", "Aria", "Sebastian", "Penelope", "Cooper", "Hazel",
    "Levi", "Nora", "Hudson", "Scarlett", "Chase", "Eleanor", "Grayson", "Lucy",
    "Maxwell", "Paisley", "Easton", "Kennedy", "Liam", "Sadie", "Oliver", "Aaliyah",
    "Aiden", "Piper", "Jackson", "Autumn", "Jayden", "Ruby", "Lincoln", "Stella",
    "Nolan", "Aurora", "Miles", "Alice", "Parker", "Vivian", "Tristan", "Madelyn",
    "Xavier", "Ellie", "Sawyer", "Clara", "Brayden", "Eva", "Brody", "Naomi",
    "Declan", "Lydia", "Bentley", "Faith", "Vincent", "Isla", "Harrison", "Quinn"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson",
    "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker",
    "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill",
    "Flores", "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell",
    "Mitchell", "Carter", "Roberts", "Turner", "Phillips", "Parker", "Evans", "Edwards",
    "Collins", "Stewart", "Morris", "Rogers", "Reed", "Cook", "Morgan", "Bell",
    "Murphy", "Bailey", "Cooper", "Richardson", "Cox", "Howard", "Ward", "Diaz",
    "Peterson", "Gray", "James", "Watson", "Brooks", "Kelly", "Sanders", "Price",
    "Bennett", "Wood", "Barnes", "Ross", "Henderson", "Coleman", "Jenkins", "Perry",
    "Powell", "Long", "Patterson", "Hughes", "Washington", "Butler", "Simmons", "Foster",
    "Gonzales", "Bryant", "Alexander", "Russell", "Griffin", "Hayes", "Myers", "Ford",
    "Hamilton", "Graham", "Sullivan", "Wallace", "Woods", "Cole", "West", "Jordan",
    "Owens", "Reynolds", "Fisher", "Ellis", "Harrison", "Gibson", "McDonald", "Cruz",
    "Marshall", "Ortiz", "Gomez", "Murray", "Freeman", "Wells", "Webb", "Simpson",
    "Stevens", "Tucker", "Porter", "Hunter", "Hicks", "Crawford", "Henry", "Boyd",
    "Mason", "Morales", "Kennedy", "Warren", "Dixon", "Ramos", "Reyes", "Burns",
    "Gordon", "Shaw", "Holmes", "Rice", "Robertson", "Hunt", "Black", "Daniels",
    "Palmer", "Mills", "Nichols", "Grant", "Knight", "Ferguson", "Rose", "Stone",
    "Hawkins", "Dunn", "Perkins", "Hudson", "Spencer", "Gardner", "Stephens", "Payne",
    "Pierce", "Berry", "Matthews", "Arnold", "Wagner", "Willis", "Ray", "Watkins",
    "Olson", "Carroll", "Duncan", "Snyder", "Hart", "Cunningham", "Bradley", "Lane",
    "Andrews", "Ruiz", "Harper", "Fox", "Riley", "Armstrong", "Carpenter", "Weaver",
    "Greene", "Lawrence", "Elliott", "Chavez", "Sims", "Austin", "Peters", "Kelley",
    "Franklin", "Lawson", "Fields", "Gutierrez", "Ryan", "Schmidt", "Carr", "Vasquez",
    "Castillo", "Wheeler", "Chapman", "Oliver", "Montgomery", "Richards", "Williamson", "Johnston",
    "Banks", "Meyer", "Bishop", "McCoy", "Howell", "Alvarez", "Morrison", "Hansen"
]

# Realistic websites with categories
WEBSITES = {
    "work": [
        ("https://github.com", "GitHub"),
        ("https://gitlab.com", "GitLab"),
        ("https://bitbucket.org", "Bitbucket"),
        ("https://stackoverflow.com", "Stack Overflow"),
        ("https://docs.microsoft.com", "Microsoft Docs"),
        ("https://developer.mozilla.org", "MDN Web Docs"),
        ("https://aws.amazon.com", "Amazon Web Services"),
        ("https://portal.azure.com", "Microsoft Azure"),
        ("https://console.cloud.google.com", "Google Cloud Console"),
        ("https://confluence.company.com", "Company Confluence"),
        ("https://jira.company.com", "Company JIRA"),
        ("https://teams.microsoft.com", "Microsoft Teams"),
        ("https://slack.com", "Slack"),
        ("https://zoom.us", "Zoom"),
        ("https://trello.com", "Trello"),
        ("https://asana.com", "Asana"),
        ("https://notion.so", "Notion"),
        ("https://monday.com", "Monday.com"),
        ("https://clickup.com", "ClickUp"),
        ("https://basecamp.com", "Basecamp"),
        ("https://figma.com", "Figma"),
        ("https://miro.com", "Miro"),
        ("https://canva.com", "Canva"),
        ("https://adobe.com", "Adobe Creative Cloud"),
    ],
    "research": [
        ("https://wikipedia.org", "Wikipedia"),
        ("https://scholar.google.com", "Google Scholar"),
        ("https://researchgate.net", "ResearchGate"),
        ("https://arxiv.org", "arXiv"),
        ("https://pubmed.ncbi.nlm.nih.gov", "PubMed"),
        ("https://ieee.org", "IEEE Xplore"),
        ("https://acm.org", "ACM Digital Library"),
        ("https://springerlink.com", "Springer Link"),
        ("https://sciencedirect.com", "ScienceDirect"),
        ("https://jstor.org", "JSTOR"),
        ("https://nature.com", "Nature"),
        ("https://science.org", "Science Magazine"),
        ("https://semanticscholar.org", "Semantic Scholar"),
        ("https://mendeley.com", "Mendeley"),
        ("https://wolframalpha.com", "Wolfram Alpha"),
    ],
    "news": [
        ("https://bbc.com", "BBC News"),
        ("https://cnn.com", "CNN"),
        ("https://reuters.com", "Reuters"),
        ("https://techcrunch.com", "TechCrunch"),
        ("https://arstechnica.com", "Ars Technica"),
        ("https://wired.com", "Wired"),
        ("https://theverge.com", "The Verge"),
        ("https://nytimes.com", "New York Times"),
        ("https://wsj.com", "Wall Street Journal"),
        ("https://bloomberg.com", "Bloomberg"),
        ("https://forbes.com", "Forbes"),
        ("https://techradar.com", "TechRadar"),
        ("https://engadget.com", "Engadget"),
        ("https://mashable.com", "Mashable"),
        ("https://hackernews.com", "Hacker News"),
    ],
    "productivity": [
        ("https://gmail.com", "Gmail"),
        ("https://outlook.com", "Outlook"),
        ("https://calendar.google.com", "Google Calendar"),
        ("https://drive.google.com", "Google Drive"),
        ("https://dropbox.com", "Dropbox"),
        ("https://onedrive.live.com", "OneDrive"),
        ("https://evernote.com", "Evernote"),
        ("https://onenote.com", "OneNote"),
        ("https://box.com", "Box"),
        ("https://todoist.com", "Todoist"),
        ("https://any.do", "Any.do"),
        ("https://airtable.com", "Airtable"),
        ("https://coda.io", "Coda"),
    ],
    "casual": [
        ("https://youtube.com", "YouTube"),
        ("https://reddit.com", "Reddit"),
        ("https://twitter.com", "Twitter"),
        ("https://linkedin.com", "LinkedIn"),
        ("https://facebook.com", "Facebook"),
        ("https://instagram.com", "Instagram"),
        ("https://netflix.com", "Netflix"),
        ("https://spotify.com", "Spotify"),
        ("https://tiktok.com", "TikTok"),
        ("https://twitch.tv", "Twitch"),
        ("https://discord.com", "Discord"),
        ("https://pinterest.com", "Pinterest"),
        ("https://medium.com", "Medium"),
        ("https://dev.to", "DEV Community"),
        ("https://imgur.com", "Imgur"),
        ("https://vimeo.com", "Vimeo"),
        ("https://soundcloud.com", "SoundCloud"),
    ]
}

# Source tags matching real Windows agent client values
BROWSER_SETUPS = [
    # (label, sources, weight)
    ("chrome_only", ["windows_agent"], 40),
    ("edge_only", ["windows_agent_edge"], 30),
    ("both", ["windows_agent", "windows_agent_edge"], 30),
]

BROWSER_PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3"]

COMPUTER_NAMES = [
    "DESKTOP-ABC123", "LAPTOP-XYZ789", "WORKSTATION-001", "PC-OFFICE-01",
    "DEV-MACHINE-02", "ANALYST-PC", "ADMIN-LAPTOP", "RESEARCHER-01",
    "STUDENT-PC-01", "FACULTY-LAPTOP", "LAB-COMPUTER-A", "OFFICE-DESKTOP",
    "REMOTE-LAPTOP", "HOME-OFFICE-PC", "MOBILE-WORKSTATION", "CONFERENCE-PC",
    "TRAINING-PC-01", "BACKUP-MACHINE", "TEST-COMPUTER", "SHARED-WORKSTATION",
    "MACBOOK-PRO-01", "IMAC-STUDIO", "THINKPAD-T14", "SURFACE-LAPTOP-5",
    "HP-ELITEBOOK", "DELL-PRECISION", "LENOVO-YOGA", "ASUS-ZENBOOK",
    "ACER-ASPIRE", "RAZER-BLADE", "MSI-PRESTIGE", "FRAMEWORK-LAPTOP",
    "ALIENWARE-M15", "CHROMEBOOK-01", "TABLET-SURFACE", "IPAD-PRO-12",
    "HOME-PC-WIN11", "LAB-MAC-MINI", "SERVER-TERM-01", "KIOSK-LOBBY"
]

def generate_user_data(user_index: int, homegroup: str, used_names: set) -> Dict:
    """Generate user data for a specific user with unique name combinations"""
    # Generate a unique name combination for this user
    max_attempts = 100
    for _ in range(max_attempts):
        first_name = random.choice(FIRST_NAMES)
        last_name = random.choice(LAST_NAMES)
        name_key = f"{first_name}_{last_name}"

        if name_key not in used_names:
            used_names.add(name_key)
            break

    # Create username with homegroup identifier for uniqueness
    username = f"{first_name.lower()}.{last_name.lower()}.{homegroup.lower()}"

    return {
        "Username": username,
        "DisplayName": f"{first_name} {last_name}",
        "FirstName": first_name,
        "LastName": last_name,
        "Department": homegroup,
        "Email": f"{username}@company.com"
    }

def generate_browsing_pattern() -> List[str]:
    """Generate a realistic browsing pattern for a user"""
    # Different user types have different browsing patterns
    patterns = [
        # Developer/IT pattern
        ["work", "work", "work", "research", "productivity", "news", "casual"],
        # Researcher pattern  
        ["research", "research", "work", "productivity", "news", "work", "casual"],
        # Manager pattern
        ["productivity", "work", "news", "work", "productivity", "casual", "news"],
        # General office pattern
        ["productivity", "work", "news", "casual", "productivity", "work", "casual"],
        # Student pattern
        ["research", "casual", "productivity", "research", "news", "casual", "work"]
    ]
    return random.choice(patterns)

def pick_browser_setup() -> tuple:
    """Pick a browser setup (chrome-only, edge-only, or both) based on weights"""
    labels, sources_list, weights = zip(*BROWSER_SETUPS)
    chosen = random.choices(range(len(BROWSER_SETUPS)), weights=weights, k=1)[0]
    return labels[chosen], sources_list[chosen]


def generate_visits(user_info: Dict, num_visits: int, browser_sources: List[str]) -> List[Dict]:
    """Generate realistic browsing visits for a user"""
    visits = []
    browsing_pattern = generate_browsing_pattern()

    # Users may use multiple computers
    primary_computer = random.choice(COMPUTER_NAMES)
    use_multiple_computers = random.random() < 0.3  # 30% chance of using multiple computers

    # Browser profile — most users on Default, some with extra profiles
    if random.random() < 0.15:
        user_profiles = random.sample(BROWSER_PROFILES, k=random.randint(2, 3))
    else:
        user_profiles = ["Default"]

    # Generate visits over the last 90 days for more spread
    end_time = datetime.now()
    start_time = end_time - timedelta(days=90)

    for i in range(num_visits):
        # Pick category based on browsing pattern (but allow some randomness)
        if random.random() < 0.85:
            category = random.choice(browsing_pattern)
        else:
            category = random.choice(list(WEBSITES.keys()))

        url, title = random.choice(WEBSITES[category])

        # Some users work on weekends, most don't
        works_weekends = random.random() < 0.2

        # Generate realistic timing with varied patterns
        day_offset = random.randint(0, 89)
        selected_day = start_time + timedelta(days=day_offset)

        # Skip weekends for most users
        if not works_weekends and selected_day.weekday() >= 5:
            day_offset = (day_offset - (selected_day.weekday() - 4)) % 90
            selected_day = start_time + timedelta(days=day_offset)

        # Varied hour patterns - some early birds, some night owls
        hour_pattern = random.choices(
            [[7,8,9,10,11,12,13,14,15,16], [9,10,11,12,13,14,15,16,17,18], [10,11,12,13,14,15,16,17,18,19]],
            weights=[20, 60, 20]
        )[0]

        random_time = selected_day.replace(
            hour=random.choice(hour_pattern),
            minute=random.randint(0, 59),
            second=random.randint(0, 59)
        )

        # Select computer
        if use_multiple_computers and random.random() < 0.2:
            computer_name = random.choice(COMPUTER_NAMES)
        else:
            computer_name = primary_computer
        
        # Add some specific page paths for realism
        if "github.com" in url:
            paths = ["/repo/project", "/issues", "/pulls", "/wiki", "/settings"]
            url += random.choice(paths)
            title += f" - {random.choice(['Issues', 'Pull Requests', 'Wiki', 'Repository'])}"
        elif "stackoverflow.com" in url:
            url += f"/questions/{random.randint(1000000, 9999999)}"
            title = f"Programming Question - {title}"
        elif "youtube.com" in url:
            url += f"/watch?v={''.join(random.choices('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=11))}"
            title = f"Video: {random.choice(['Tutorial', 'Review', 'News', 'Entertainment'])} - {title}"
        
        visits.append({
            "Url": url,
            "Title": title,
            "VisitTime": int(random_time.timestamp() * 1000),  # Convert to milliseconds
            "ComputerName": computer_name,
            "Source": random.choice(browser_sources),
            "BrowserProfile": random.choice(user_profiles),
        })
    
    # Sort visits by time
    visits.sort(key=lambda x: x["VisitTime"])
    return visits

def send_user_data(user_info: Dict, visits: List[Dict]) -> bool:
    """Send user data to the API"""
    payload = {
        "Username": user_info["Username"],
        "Visits": visits,
        "UserInfo": user_info
    }
    
    try:
        response = requests.post(API_URL, json=payload, timeout=30)  # Increased timeout for large data
        if response.status_code == 200:
            return True
        else:
            return False
    except Exception as e:
        return False

def main():
    """Generate and send load test data for all users"""
    print("🚀 Browser Reporter Load Test Data Generator")
    print("=" * 60)
    print(f"📊 Generating data for {len(HOMEGROUPS) * USERS_PER_GROUP} users")
    print(f"🏢 Homegroups: {len(HOMEGROUPS)} groups ({USERS_PER_GROUP} users each)")
    print(f"🖥️  Sources: windows_agent (Chrome), windows_agent_edge (Edge)")
    print(f"🌐 API Endpoint: {API_URL}")
    print(f"📈 Target: 500-1500 visits per user (randomized)")
    print()

    total_users = 0
    successful_uploads = 0
    total_visits = 0
    source_counts = {"chrome_only": 0, "edge_only": 0, "both": 0}
    start_time = time.time()

    for group_index, homegroup in enumerate(HOMEGROUPS):
        print(f"📁 Processing homegroup: {homegroup} ({group_index + 1}/{len(HOMEGROUPS)})")

        # Track used names per homegroup to ensure uniqueness
        used_names_in_group = set()

        for user_in_group in range(USERS_PER_GROUP):
            user_index = group_index * USERS_PER_GROUP + user_in_group

            # Generate user data with unique names per homegroup
            user_info = generate_user_data(user_index, homegroup, used_names_in_group)

            # Assign browser setup for this user
            browser_label, browser_sources = pick_browser_setup()
            source_counts[browser_label] += 1

            # Random number of visits for variety (500-1500)
            num_visits = random.randint(500, 1500)
            visits = generate_visits(user_info, num_visits, browser_sources)

            # Send data
            if send_user_data(user_info, visits):
                successful_uploads += 1
                total_visits += len(visits)
                print(f"   ✅ [{total_users + 1:3d}/{len(HOMEGROUPS) * USERS_PER_GROUP}] {user_info['Username']} ({len(visits)} visits)")
            else:
                print(f"   ❌ [{total_users + 1:3d}/{len(HOMEGROUPS) * USERS_PER_GROUP}] FAILED: {user_info['Username']}")

            total_users += 1

            # Minimal delay for faster ingestion (50ms)
            time.sleep(0.05)
    
    end_time = time.time()
    duration = end_time - start_time

    print()
    print("=" * 60)
    print("📈 Data Generation Summary:")
    print(f"   👥 Total users: {total_users}")
    print(f"   ✅ Successful uploads: {successful_uploads}")
    print(f"   ❌ Failed uploads: {total_users - successful_uploads}")
    print(f"   📊 Total visits generated: {total_visits:,}")
    print(f"   📊 Average visits per user: {total_visits / successful_uploads if successful_uploads > 0 else 0:.0f}")
    print(f"   🏢 Homegroups: {len(HOMEGROUPS)} (1A-6C)")
    print(f"   👤 Users per homegroup: {USERS_PER_GROUP}")
    print(f"   🖥️  Source distribution:")
    print(f"      Chrome only (windows_agent): {source_counts['chrome_only']} users")
    print(f"      Edge only (windows_agent_edge): {source_counts['edge_only']} users")
    print(f"      Both browsers: {source_counts['both']} users")
    print(f"   ⏱️  Total time: {duration:.1f} seconds ({duration/60:.1f} minutes)")
    print(f"   🚀 Upload rate: {successful_uploads / duration:.2f} users/second")
    print(f"   📈 Data rate: {total_visits / duration:.0f} visits/second")

    if successful_uploads == total_users:
        print(f"\n🎉 Data generation completed successfully!")
        print(f"💪 Generated {total_visits:,} browsing records across {successful_uploads} users")
        print(f"📁 Distributed across 18 homegroups (1A-6C) with 30 users each")
        print(f"🌐 Data sent via API (realistic client simulation)")
        print("💡 View the data in the dashboard at http://localhost:8000")
    else:
        print(f"\n⚠️  {total_users - successful_uploads} uploads failed. Check the API server status.")

if __name__ == "__main__":
    main() 