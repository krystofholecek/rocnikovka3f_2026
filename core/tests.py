from django.test import TestCase
from django.urls import reverse


class HomePageTests(TestCase):
    def test_home_page_loads(self):
        response = self.client.get(reverse('core:home'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'core/home.html')
        self.assertContains(response, 'LLM 2005')
        self.assertContains(response, 'Stav projektu')
        self.assertContains(response, 'Chat bude brzy')
