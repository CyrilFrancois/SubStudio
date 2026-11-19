# stub_cues.py — A made-up English → French dialogue for the stub's cue list, with one line of each defect the gate flags. Exports: SAMPLE.
"""Written for the stub (no film's text). Seeded defects, so the Review screen shows each flag:
id 6 too fast, id 9 a line too long, id 12 left in English, id 15 broken accents, id 18 repeats id 17."""

# (start_ms, end_ms, English source, French translation)
SAMPLE = [
    (1_200, 3_400, "Is anyone home?", "Il y a quelqu'un ?"),
    (3_900, 6_100, "Upstairs. Mind the third step.", "En haut. Attention à la troisième marche."),
    (6_800, 9_500, "Why does it always creak at night?", "Pourquoi ça grince toujours la nuit ?"),
    (10_200, 12_600, "Old houses talk.\nYou get used to it.", "Les vieilles maisons parlent.\nOn s'y habitue."),
    (13_100, 15_000, "I'm not sure I want to.", "Je ne suis pas sûre de le vouloir."),
    (15_200, 15_900, "Wait, did you hear that?", "Attends, tu as entendu ça, là, juste maintenant ?"),
    (16_600, 18_900, "It's just the wind.", "C'est juste le vent."),
    (19_500, 22_000, "The wind doesn't knock twice.", "Le vent ne frappe pas deux fois."),
    (22_600, 26_400, "We should call someone before it gets dark.",
     "On devrait appeler quelqu'un avant qu'il fasse complètement nuit dehors ce soir."),
    (27_000, 29_300, "Who would we even call?", "Et on appellerait qui ?"),
    (29_900, 32_100, "Your aunt. She lived here.", "Ta tante. Elle a vécu ici."),
    (32_800, 35_600, "She never mentioned any noises.", "She never mentioned any noises."),
    (36_200, 38_400, "Maybe she liked the company.", "Peut-être qu'elle aimait la compagnie."),
    (39_000, 41_500, "That's not funny.", "Ce n'est pas drôle."),
    (42_100, 44_800, "I already made coffee.", "J'ai d?j? fait du café."),
    (45_400, 47_600, "Make it strong.", "Fais-le fort."),
    (48_200, 50_300, "Hello?", "Allô ?"),
    (50_900, 52_700, "Hello?", "Allô ?"),
    (53_400, 56_000, "Okay. Now I'm calling your aunt.", "D'accord. Là, j'appelle ta tante."),
]
