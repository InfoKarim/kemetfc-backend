from app.recommendations import generate_recommendations


def test_generate_recommendations_priorities():
    analysis = {
        "top_weaknesses": [
            ("Strength", 55),
            ("Passing", 65),
            ("Vision", 75),
        ]
    }

    recommendations = generate_recommendations(analysis)

    assert recommendations == [
        "[HIGH] Add strength and resistance training.",
        "[MEDIUM] Work on passing accuracy and decision speed.",
        "[LOW] Practice scanning and awareness before receiving the ball.",
    ]
    
def test_generate_recommendations_unknown_attribute():
    analysis = {
        "top_weaknesses": [
            ("Leadership", 55),
        ]
    }

    recommendations = generate_recommendations(analysis)

    assert recommendations == [
        "[HIGH] Work on improving Leadership."
    ]
