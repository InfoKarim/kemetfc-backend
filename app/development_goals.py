from app.player import Player
from app.analysis import analyze_player

def generate_development_goals(player: Player) -> list:
    goals = []
    analysis = analyze_player(player)
    weaknesses = analysis["top_weaknesses"]
    
    for attribute, score in weaknesses:
            goal = f"Improve {attribute} from {score} to {min(score + 5, 100)}"
            goals.append(goal)
    return goals