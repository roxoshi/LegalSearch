import os
from bs4 import BeautifulSoup

def find_issues(html_dir):
    issues = []
    for filename in os.listdir(html_dir):
        if filename.endswith(".html"):
            with open(os.path.join(html_dir, filename), "r") as f:
                soup = BeautifulSoup(f.read(), "html.parser")
                
                # Find h2 with very short content
                for h2 in soup.find_all("h2"):
                    text = h2.get_text().strip()
                    if text == "H":
                        issues.append(f"{filename} | H2 with 'H'")
                    elif len(text) == 1:
                        issues.append(f"{filename} | H2 with '{text}'")
                
                # Find A-G as single characters in p tags
                for p in soup.find_all("p"):
                    text = p.get_text().strip()
                    if text in "ABCDEFG" and len(text) == 1:
                        issues.append(f"{filename} | P with '{text}'")
    return issues

if __name__ == "__main__":
    html_dir = "output_htmls"
    issues = find_issues(html_dir)
    for issue in issues:
        print(issue)
