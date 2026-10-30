"""Creative diversity: near-identical variants are named before signing (advisory)."""

from studio.meta import diversity

GIFT = {"primary_text": "A mug made by hand for someone who keeps your secrets.",
        "headline": "The gift that remembers hands"}
CRAFT = {"primary_text": "Thrown on a wheel in Kraków, fired at 1240 degrees, glazed by Ola.",
         "headline": "Watch your mug being made"}
RITUAL = {"primary_text": "Your first coffee deserves a mug that feels like a morning walk.",
          "headline": "Slow mornings, one mug"}


def test_three_angles_are_diverse():
    result = diversity.check([GIFT, CRAFT, RITUAL], ["a", "b", "c"])
    assert result == {"verdict": "ok", "variants": 3, "distinct_images": 3,
                      "near_duplicates": []}


def test_a_paraphrase_is_a_near_duplicate():
    twin = {"primary_text": "A mug made by hand for someone who keeps all your secrets!",
            "headline": "The gift that remembers hands"}
    result = diversity.check([GIFT, CRAFT, twin], ["a", "b", "a"])
    assert result["verdict"] == "warn"
    assert result["near_duplicates"] == [{"variants": [1, 3], "similarity": 0.93,
                                          "same_image": True}]
    assert result["distinct_images"] == 2


def test_case_and_punctuation_do_not_make_a_difference():
    loud = {"primary_text": GIFT["primary_text"].upper() + "!!!",
            "headline": GIFT["headline"].upper()}
    assert diversity.check([GIFT, loud])["near_duplicates"][0]["similarity"] == 1.0
