#!/usr/bin/env python3
"""
Mock Data Generator for Browser Reporter
Generates realistic browsing data for 20 users across 4 homegroups
"""

import requests
import json
import random
import time
from datetime import datetime, timedelta
from typing import List, Dict

# Configuration
API_URL = "http://localhost:8000/api/reports/data"
API_KEY = "your-secure-api-key-here"

# Homegroups and user distribution (5 users each)
HOMEGROUPS = ["3A", "4A", "5A", "6C"]
USERS_PER_GROUP = 5

# Sample data for realistic browsing
FIRST_NAMES = [
    "John", "Jane", "Michael", "Sarah", "David", "Emma", "James", "Lisa",
    "Robert", "Emily", "William", "Ashley", "Christopher", "Jessica", "Daniel",
    "Amanda", "Matthew", "Stephanie", "Anthony", "Nicole"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson",
    "Thomas", "Taylor", "Moore", "Jackson", "Martin"
]

# Realistic websites with categories
WEBSITES = {
    "work": [
        ("https://github.com", "GitHub"),
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
    ],
    "news": [
        ("https://bbc.com", "BBC News"),
        ("https://cnn.com", "CNN"),
        ("https://reuters.com", "Reuters"),
        ("https://techcrunch.com", "TechCrunch"),
        ("https://arstechnica.com", "Ars Technica"),
        ("https://wired.com", "Wired"),
        ("https://theverge.com", "The Verge"),
    ],
    "productivity": [
        ("https://gmail.com", "Gmail"),
        ("https://outlook.com", "Outlook"),
        ("https://calendar.google.com", "Google Calendar"),
        ("https://drive.google.com", "Google Drive"),
        ("https://dropbox.com", "Dropbox"),
        ("https://onedrive.live.com", "OneDrive"),
        ("https://evernote.com", "Evernote"),
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
    ]
}

COMPUTER_NAMES = [
    "DESKTOP-ABC123", "LAPTOP-XYZ789", "WORKSTATION-001", "PC-OFFICE-01",
    "DEV-MACHINE-02", "ANALYST-PC", "ADMIN-LAPTOP", "RESEARCHER-01",
    "STUDENT-PC-01", "FACULTY-LAPTOP", "LAB-COMPUTER-A", "OFFICE-DESKTOP",
    "REMOTE-LAPTOP", "HOME-OFFICE-PC", "MOBILE-WORKSTATION", "CONFERENCE-PC",
    "TRAINING-PC-01", "BACKUP-MACHINE", "TEST-COMPUTER", "SHARED-WORKSTATION"
]

def generate_user_data(user_index: int, homegroup: str) -> Dict:
    """Generate user data for a specific user"""
    first_name = FIRST_NAMES[user_index]
    last_name = LAST_NAMES[user_index]
    username = f"{first_name.lower()}.{last_name.lower()}"
    
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

def generate_visits(user_info: Dict, num_visits: int) -> List[Dict]:
    """Generate realistic browsing visits for a user"""
    visits = []
    browsing_pattern = generate_browsing_pattern()
    computer_name = random.choice(COMPUTER_NAMES)
    
    # Generate visits over the last 30 days
    end_time = datetime.now()
    start_time = end_time - timedelta(days=30)
    
    for i in range(num_visits):
        # Pick category based on browsing pattern
        category = random.choice(browsing_pattern)
        url, title = random.choice(WEBSITES[category])
        
        # Generate realistic timing (business hours weighted)
        random_time = start_time + timedelta(
            days=random.randint(0, 29),
            hours=random.choices([8, 9, 10, 11, 12, 13, 14, 15, 16, 17], 
                                weights=[5, 10, 15, 15, 10, 10, 15, 15, 10, 5])[0],
            minutes=random.randint(0, 59),
            seconds=random.randint(0, 59)
        )
        
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
            "ComputerName": computer_name
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
    
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": API_KEY
    }
    
    try:
        response = requests.post(API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            print(f"✅ Successfully sent data for {user_info['Username']} ({len(visits)} visits)")
            return True
        else:
            print(f"❌ Failed to send data for {user_info['Username']}: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending data for {user_info['Username']}: {e}")
        return False

def main():
    """Generate and send mock data for all users"""
    print("🚀 Browser Reporter Mock Data Generator")
    print("=" * 50)
    print(f"📊 Generating data for {len(HOMEGROUPS) * USERS_PER_GROUP} users")
    print(f"🏢 Homegroups: {', '.join(HOMEGROUPS)} ({USERS_PER_GROUP} users each)")
    print(f"🌐 API Endpoint: {API_URL}")
    print()
    
    total_users = 0
    successful_uploads = 0
    total_visits = 0
    
    for group_index, homegroup in enumerate(HOMEGROUPS):
        print(f"📁 Processing homegroup: {homegroup}")
        
        for user_in_group in range(USERS_PER_GROUP):
            user_index = group_index * USERS_PER_GROUP + user_in_group
            
            # Generate user data
            user_info = generate_user_data(user_index, homegroup)
            
            # Generate random number of visits (20-30)
            num_visits = random.randint(20, 30)
            visits = generate_visits(user_info, num_visits)
            
            # Send data
            if send_user_data(user_info, visits):
                successful_uploads += 1
                total_visits += len(visits)
            
            total_users += 1
            
            # Small delay to avoid overwhelming the server
            time.sleep(0.5)
    
    print()
    print("📈 Summary:")
    print(f"   👥 Total users: {total_users}")
    print(f"   ✅ Successful uploads: {successful_uploads}")
    print(f"   ❌ Failed uploads: {total_users - successful_uploads}")
    print(f"   📊 Total visits generated: {total_visits}")
    print(f"   📊 Average visits per user: {total_visits / successful_uploads if successful_uploads > 0 else 0:.1f}")
    
    if successful_uploads == total_users:
        print("\n🎉 All mock data generated successfully!")
        print("💡 You can now view the data in the dashboard at http://localhost:8000")
    else:
        print(f"\n⚠️  Some uploads failed. Check the API server status.")

if __name__ == "__main__":
    main() 