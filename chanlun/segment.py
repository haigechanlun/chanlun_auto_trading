class Segment:

    @staticmethod
    def generate(bis):

        segments = []

        if len(bis) < 3:
            return segments

        for i in range(2, len(bis)):

            seg = {
                "start": bis[i-2],
                "mid": bis[i-1],
                "end": bis[i]
            }

            segments.append(seg)

        return segments