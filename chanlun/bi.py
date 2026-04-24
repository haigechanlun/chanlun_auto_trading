class Bi:

    @staticmethod
    def generate(fractals):

        bis = []

        for i in range(1, len(fractals)):

            f1 = fractals[i-1]
            f2 = fractals[i]

            if f1["type"] != f2["type"]:

                bis.append({
                    "start":f1,
                    "end":f2,
                    "direction":"up" if f1["type"]=="bottom" else "down"
                })

        return bis

        