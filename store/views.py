from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib import messages
from django.conf import settings
from .models import Feedback
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from rdflib import Graph, URIRef, Namespace, Literal
from rdflib.namespace import RDF, XSD
from django.core.paginator import Paginator
import uuid
import os
import json
import re
import google.generativeai as genai
from datetime import datetime


# Add to imports at top:
import google.generativeai as genai


class BaseOntologyView(View):
    def __init__(self):
        super().__init__()
        self.graph = Graph()
        self.ontology_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 
                                         'ontology', 'Ecommerce_Platform.xml')
        try:
            self.graph.parse(self.ontology_path)
        except Exception as e:
            print(f"Error loading ontology: {e}")
        self.ECOM_NS = Namespace("http://www.example.org/ecommerce_ontology#")
        
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-pro')
    
    def save_graph(self):
        try:
            self.graph.serialize(destination=self.ontology_path, format="xml")
        except Exception as e:
            print(f"Error saving ontology: {e}")
            raise

    def get_popular_products(self):
        products = {}
        for order in self.graph.subjects(RDF.type, self.ECOM_NS.Order):
            product = self.graph.value(order, self.ECOM_NS.product)
            if product:
                product_name = str(self.graph.value(product, self.ECOM_NS.name))
                products[product_name] = products.get(product_name, 0) + 1
        return sorted(products.items(), key=lambda x: x[1], reverse=True)
    
    def get_product_recommendations(self, user_preferences):
        products = []
        for product in self.graph.subjects(RDF.type, self.ECOM_NS.Product):
            try:
                product_data = {
                    'name': str(self.graph.value(product, self.ECOM_NS.name)),
                    'price': float(self.graph.value(product, self.ECOM_NS.price)),
                    'stock': int(self.graph.value(product, self.ECOM_NS.stockLevel)),
                    'discount': float(self.graph.value(product, self.ECOM_NS.discount, default=Literal(0.0)))
                }
                products.append(product_data)
            except Exception as e:
                print(f"Error processing product {product}: {e}")
                continue

        prompt = f"""
        Given these products: {json.dumps(products)}
        And user preferences: {json.dumps(user_preferences)}
        Recommend the top 3 products that best match the user's preferences.
        Explain why each product is recommended.
        Format your response as JSON with 'recommendations' array containing objects with 
        'product_name' and 'reason' fields.
        """
        
        try:
            response = self.model.generate_content(prompt)
            return json.loads(response.text)
        except Exception as e:
            print(f"Error getting AI recommendations: {e}")
            return {"recommendations": []}
    
    def process_customer_query(self, query):
        context = {
            "popular_products": self.get_popular_products(),
            "total_products": len(list(self.graph.subjects(RDF.type, self.ECOM_NS.Product))),
            "total_orders": len(list(self.graph.subjects(RDF.type, self.ECOM_NS.Order))),
            "total_customers": len(list(self.graph.subjects(RDF.type, self.ECOM_NS.Customer)))
        }
        
        prompt = f"""
        Given this e-commerce data: {json.dumps(context)}
        And this customer query: "{query}"
        Provide a helpful, natural response based on the data.
        If specific data isn't available, provide general guidance.
        """
        
        try:
            response = self.model.generate_content(prompt)
            return response.text
        except Exception as e:
            print(f"Error processing query: {e}")
            return "I apologize, but I'm having trouble processing your request right now."

    def log_interaction(self, user, query, response):
        interaction_id = str(datetime.now().timestamp())
        interaction_uri = URIRef(self.ECOM_NS + f"chat_interaction_{interaction_id}")
        
        interaction_data = [
            (RDF.type, self.ECOM_NS.ChatInteraction),
            (self.ECOM_NS.user, Literal(user, datatype=XSD.string)),
            (self.ECOM_NS.query, Literal(query, datatype=XSD.string)),
            (self.ECOM_NS.response, Literal(response, datatype=XSD.string)),
            (self.ECOM_NS.timestamp, Literal(datetime.now().isoformat(), datatype=XSD.dateTime))
        ]
        
        for predicate, obj in interaction_data:
            self.graph.add((interaction_uri, predicate, obj))
        self.save_graph()
        
class LoginView(View):
    """Handle user and admin authentication"""
    def get(self, request):
        # Clear any existing session and logout
        logout(request)
        request.session.flush()
        
        return render(request, 'store/index.html')
    
    def post(self, request):
        form_type = request.POST.get('form_type', 'user')
        username = request.POST.get(f'{form_type}_name')
        password = request.POST.get(f'{form_type}_password')
        
        # In production, replace with proper authentication
        credentials = {
            'user': {'username': 'JohnDoe', 'password': 'JohnDoe'},
            'admin': {'username': 'Admin', 'password': 'Admin'}
        }
        
        if (username == credentials[form_type]['username'] and 
            password == credentials[form_type]['password']):
            # Clear any existing session first
            request.session.flush()
            
            # Set session data
            request.session['user_type'] = form_type
            request.session['username'] = username
            
            # Handle Django authentication
            from django.contrib.auth.models import User
            user, created = User.objects.get_or_create(username=username)
            login(request, user)
            
            return redirect('baseUser' if form_type == 'user' else 'baseAdmin')
        
        messages.error(request, f'Invalid {form_type} credentials')
        return render(request, 'store/index.html', {'error': f'Invalid {form_type} credentials'})


class YellowPagesAgent(BaseOntologyView):
    def __init__(self):
        super().__init__()
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-pro')
        
    def get_product_info(self, product_name=None):
        """Get detailed information about a specific product or all products"""
        products = []
        for product in self.graph.subjects(RDF.type, self.ECOM_NS.Product):
            try:
                name = str(self.graph.value(product, self.ECOM_NS.name))
                if product_name and name.lower() != product_name.lower():
                    continue
                    
                product_data = {
                    'name': name,
                    'price': float(self.graph.value(product, self.ECOM_NS.price)),
                    'stock': int(self.graph.value(product, self.ECOM_NS.stockLevel)),
                    'discount': float(self.graph.value(product, self.ECOM_NS.discount, default=Literal(0.0)))
                }
                product_data['final_price'] = round(
                    product_data['price'] * (1 - product_data['discount']/100), 2
                )
                products.append(product_data)
            except Exception as e:
                print(f"Error processing product {product}: {e}")
                continue
                
        return products[0] if product_name and products else products
    
    def get_discounted_products(self):
        """Get all products with active discounts"""
        products = []
        for product in self.graph.subjects(RDF.type, self.ECOM_NS.Product):
            try:
                discount = float(self.graph.value(product, self.ECOM_NS.discount, default=Literal(0.0)))
                if discount > 0:
                    products.append({
                        'name': str(self.graph.value(product, self.ECOM_NS.name)),
                        'price': float(self.graph.value(product, self.ECOM_NS.price)),
                        'discount': discount,
                        'final_price': float(self.graph.value(product, self.ECOM_NS.price)) * 
                                     (1 - discount/100)
                    })
            except Exception as e:
                print(f"Error processing product {product}: {e}")
                continue
        return sorted(products, key=lambda x: x['discount'], reverse=True)
    
    def process_customer_query(self, query, user=None):
        """Process customer queries with enhanced understanding"""
        try:
            # Get context data
            context = {
                'products': self.get_product_info(),
                'discounted_products': self.get_discounted_products(),
                'total_products': len(list(self.graph.subjects(RDF.type, self.ECOM_NS.Product))),
                'popular_products': self.get_popular_products()
            }
            
            # Clean and normalize the query
            query = query.lower().strip()
            
            # Direct product price query
            if "price" in query or "cost" in query:
                product_name = self._extract_product_name(query)
                if product_name:
                    product = self.get_product_info(product_name)
                    if product:
                        response = f"{product['name']} costs ${product['price']}"
                        if product['discount'] > 0:
                            response += f" (${product['final_price']} after {product['discount']}% discount)"
                        return response
            
            # Discount-related queries
            if "discount" in query or "sale" in query or "offer" in query:
                if "highest" in query or "best" in query or "maximum" in query:
                    discounted = self.get_discounted_products()
                    if discounted:
                        product = discounted[0]
                        return (f"The highest discount is on {product['name']} "
                               f"with {product['discount']}% off, "
                               f"reducing the price from ${product['price']} to "
                               f"${product['final_price']}")
                    return "Currently there are no discounted products."
            
            # Stock availability
            if "stock" in query or "available" in query:
                product_name = self._extract_product_name(query)
                if product_name:
                    product = self.get_product_info(product_name)
                    if product:
                        return (f"{product['name']} has {product['stock']} units in stock" 
                               if product['stock'] > 0 else 
                               f"Sorry, {product['name']} is currently out of stock")
            
            # Use AI for more complex queries
            prompt = f"""
            Given this e-commerce data: {json.dumps(context)}
            And this customer query: "{query}"
            Provide a helpful, natural response based on the data.
            Focus only on public product information and avoid sharing sensitive data.
            If specific data isn't available, provide general guidance.
            """
            
            response = self.model.generate_content(prompt)
            
            # Log interaction if user is provided
            if user and not any(sensitive in query.lower() 
                              for sensitive in ['password', 'email', 'address', 'phone']):
                self.log_interaction(user, query, response.text)
                
            return response.text
            
        except Exception as e:
            print(f"Error processing query: {e}")
            return "I apologize, but I'm having trouble processing your request right now."
    
    def _extract_product_name(self, query):
        """Extract product name from query by matching against known products"""
        products = self.get_product_info()
        for product in products:
            if product['name'].lower() in query.lower():
                return product['name']
        return None
    
    def log_interaction(self, user, query, response):
        """Log customer interactions while respecting privacy"""
        # Remove any potential sensitive information before logging
        clean_query = self._sanitize_text(query)
        clean_response = self._sanitize_text(response)
        
        interaction_id = str(datetime.now().timestamp())
        interaction_uri = URIRef(self.ECOM_NS + f"chat_interaction_{interaction_id}")
        
        interaction_data = [
            (RDF.type, self.ECOM_NS.ChatInteraction),
            (self.ECOM_NS.user, Literal(user, datatype=XSD.string)),
            (self.ECOM_NS.query, Literal(clean_query, datatype=XSD.string)),
            (self.ECOM_NS.response, Literal(clean_response, datatype=XSD.string)),
            (self.ECOM_NS.timestamp, Literal(datetime.now().isoformat(), datatype=XSD.dateTime))
        ]
        
        for predicate, obj in interaction_data:
            self.graph.add((interaction_uri, predicate, obj))
        self.save_graph()
    
    def _sanitize_text(self, text):
        """Remove potentially sensitive information from text"""
        # Remove email addresses
        text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '[EMAIL]', text)
        # Remove phone numbers
        text = re.sub(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', '[PHONE]', text)
        # Remove potential credit card numbers
        text = re.sub(r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b', '[CARD]', text)
        return text

class ChatbotView(LoginRequiredMixin, BaseOntologyView):
    def get(self, request):
        return render(request, 'store/chat.html')

@csrf_exempt
def chat_api(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            query = data.get('query')
            user = request.user.username if request.user.is_authenticated else 'Anonymous'
            
            agent = YellowPagesAgent()  # Use the enhanced agent
            response = agent.process_customer_query(query, user)
            
            return JsonResponse({'response': response})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid request method'}, status=405)

class AddFeedbackView(LoginRequiredMixin, BaseOntologyView):
    """Handle user feedback submission with ontology integration"""
    def get(self, request):
        if request.session.get('user_type') != 'user':
            raise PermissionDenied
            
        context = {
            'range': range(5),  # For star rating display
        }
        return render(request, 'store/user/add_feedback.html', context)
    
    def post(self, request):
        try:
            # Validate user session
            if request.session.get('user_type') != 'user':
                raise PermissionDenied
                
            # Get form data
            name = request.POST.get('name')
            email = request.POST.get('email')
            rating = request.POST.get('rating')
            feedback_text = request.POST.get('feedback')
            
            # Validate required fields
            if not all([name, email, rating, feedback_text]):
                messages.error(request, 'All fields are required')
                return redirect('add_feedback')
                
            # Validate rating
            try:
                rating = int(rating)
                if not 1 <= rating <= 5:
                    raise ValueError
            except (TypeError, ValueError):
                messages.error(request, 'Invalid rating value')
                return redirect('add_feedback')
                
            # Generate UUID first - this will be used for both database and ontology
            feedback_id = str(uuid.uuid4())
            
            # Create feedback in database with the same UUID
            feedback = Feedback.objects.create(
                id=feedback_id,  # Use the UUID as the primary key
                user=name,
                rating=rating,
                comment=feedback_text
            )
            
            # Add feedback to ontology using the same UUID
            feedback_uri = URIRef(self.ECOM_NS + feedback_id)
            
            # Add feedback properties to graph
            feedback_properties = [
                (RDF.type, self.ECOM_NS.Feedback),
                (self.ECOM_NS.feedbackUser, Literal(name, datatype=XSD.string)),
                (self.ECOM_NS.userEmail, Literal(email, datatype=XSD.string)),
                (self.ECOM_NS.rating, Literal(rating, datatype=XSD.integer)),
                (self.ECOM_NS.comment, Literal(feedback_text, datatype=XSD.string)),
                (self.ECOM_NS.submissionDate, Literal(datetime.now().isoformat(), datatype=XSD.dateTime))
            ]
            
            for predicate, obj in feedback_properties:
                self.graph.add((feedback_uri, predicate, obj))
                
            self.save_graph()
            
            messages.success(request, 'Thank you for your feedback!')
            return redirect('add_feedback')
            
        except Exception as e:
            messages.error(request, f'Error submitting feedback: {str(e)}')
            return redirect('add_feedback')


class FeedbackView(LoginRequiredMixin, BaseOntologyView):
    """View for handling feedback display and management with ontology integration"""
    def get(self, request):
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied
            
        feedbacks = []
        # Query all feedback from ontology
        for feedback in self.graph.subjects(RDF.type, self.ECOM_NS.Feedback):
            try:
                feedback_data = {
                    'id': str(feedback).split('#')[-1],
                    'user': str(self.graph.value(feedback, self.ECOM_NS.feedbackUser)),
                    'email': str(self.graph.value(feedback, self.ECOM_NS.userEmail)),
                    'rating': int(self.graph.value(feedback, self.ECOM_NS.rating)),
                    'comment': str(self.graph.value(feedback, self.ECOM_NS.comment)),
                    'created_at': self.graph.value(feedback, self.ECOM_NS.submissionDate)
                }
                feedbacks.append(feedback_data)
            except Exception as e:
                print(f"Error processing feedback {feedback}: {str(e)}")
                continue
        
        # Sort feedbacks by submission date (newest first)
        feedbacks.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Paginate the feedback list
        paginator = Paginator(feedbacks, 10)
        page = request.GET.get('page')
        paginated_feedbacks = paginator.get_page(page)
        
        context = {
            'feedbacks': paginated_feedbacks,
            'star_range': range(5)
        }
        return render(request, 'store/admin/feedbacks.html', context)
    
    def post(self, request):
        """Handle feedback deletion from both database and ontology"""
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied
            
        try:
            feedback_id = request.POST.get('feedback_id')
            if not feedback_id:
                messages.error(request, 'Feedback ID is required')
                return redirect('view_feedbacks')
            
            # Create the full URI for the feedback
            feedback_uri = URIRef(self.ECOM_NS + feedback_id)
            
            # Check if feedback exists in the ontology
            if (feedback_uri, RDF.type, self.ECOM_NS.Feedback) not in self.graph:
                messages.error(request, 'Feedback not found in the system')
                return redirect('view_feedbacks')
            
            # Delete from database (if it exists)
            try:
                feedback = get_object_or_404(Feedback, id=feedback_id)
                feedback.delete()
            except Exception as e:
                print(f"Database deletion error (non-critical): {str(e)}")
            
            # Delete from ontology
            for p, o in list(self.graph.predicate_objects(feedback_uri)):
                self.graph.remove((feedback_uri, p, o))
            
            self.save_graph()
            
            messages.success(request, 'Feedback deleted successfully')
            
        except Exception as e:
            messages.error(request, f'Error deleting feedback: {str(e)}')
        
        return redirect('view_feedbacks')

class UserDashboardView(LoginRequiredMixin, BaseOntologyView):
    """User dashboard view with integrated product display"""
    def get(self, request):
        if request.session.get('user_type') != 'user':
            raise PermissionDenied
        
        # Create instance of ProductView to access product methods
        product_view = UserProductView()
        promotional_products, regular_products = product_view.get_products_by_discount()
        
        context = {
            'username': request.session.get('username'),
            'last_login': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'promotional_products': promotional_products,
            'regular_products': regular_products,
            'MEDIA_URL': settings.MEDIA_URL
        }
        return render(request, 'store/baseUser.html', context)

class AdminDashboardView(LoginRequiredMixin, BaseOntologyView):
    """Admin dashboard view"""
    def get(self, request):
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied
        return render(request, 'store/baseAdmin.html')

class ProductView(BaseOntologyView):
    """Base class for product views"""
    def get_products_by_discount(self):
        promotional_products = []
        regular_products = []
        
        for product in self.graph.subjects(RDF.type, self.ECOM_NS.Product):
            try:
                product_data = {
                    'name': str(self.graph.value(product, self.ECOM_NS.name)),
                    'price': float(self.graph.value(product, self.ECOM_NS.price)),
                    'stock': int(self.graph.value(product, self.ECOM_NS.stockLevel)),
                    'discount': float(self.graph.value(product, self.ECOM_NS.discount, 
                                                     default=Literal(0.0))),
                    'image': str(self.graph.value(product, self.ECOM_NS.hasImage, 
                                                default=Literal("default_image.jpg")))
                }
                product_data['final_price'] = round(
                    product_data['price'] * (1 - product_data['discount']/100), 2
                )
                
                # Separate products based on discount
                if product_data['discount'] > 0:
                    promotional_products.append(product_data)
                else:
                    regular_products.append(product_data)
                    
            except Exception as e:
                print(f"Error processing product {product}: {e}")
                continue
                
        return promotional_products, regular_products

class UserProductView(LoginRequiredMixin, ProductView):
    """User product listing view"""
    def get(self, request):
        if request.session.get('user_type') != 'user':
            raise PermissionDenied
            
        promotional_products, regular_products = self.get_products_by_discount()
        
        return render(request, 'store/user/userproducts.html', {
            'promotional_products': promotional_products,
            'regular_products': regular_products,
            'MEDIA_URL': settings.MEDIA_URL
        })

class AdminProductView(LoginRequiredMixin, ProductView):
    
    def get_all_products(self):
        products = []
        for product in self.graph.subjects(RDF.type, self.ECOM_NS.Product):
            try:
                product_id = str(product).split('#')[-1]
                product_data = {
                    'id': product_id,
                    'name': str(self.graph.value(product, self.ECOM_NS.name)),
                    'price': float(self.graph.value(product, self.ECOM_NS.price)),
                    'stock': int(self.graph.value(product, self.ECOM_NS.stockLevel)),
                    'discount': float(self.graph.value(product, self.ECOM_NS.discount, default=Literal(0.0))),
                    'image': str(self.graph.value(product, self.ECOM_NS.hasImage, default=Literal("default_image.jpg")))
                }
                products.append(product_data)
            except Exception as e:
                print(f"Error processing product {product}: {e}")
                continue
        return products
    
    def get(self, request, product_id=None):
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied
        
        if product_id:
            try:
                product_uri = URIRef(self.ECOM_NS + product_id)
                if (product_uri, RDF.type, self.ECOM_NS.Product) not in self.graph:
                    messages.error(request, 'Product not found')
                    return redirect('admin_product_list')
                
                product = {
                    'id': product_id,
                    'name': str(self.graph.value(product_uri, self.ECOM_NS.name)),
                    'price': float(self.graph.value(product_uri, self.ECOM_NS.price)),
                    'stock': int(self.graph.value(product_uri, self.ECOM_NS.stockLevel)),
                    'discount': float(self.graph.value(product_uri, self.ECOM_NS.discount, default=Literal(0.0))),
                    'image': str(self.graph.value(product_uri, self.ECOM_NS.hasImage, default=Literal("default_image.jpg")))
                }
                return render(request, 'store/admin/update_product.html', {'product': product})
            except Exception as e:
                messages.error(request, f'Error loading product: {str(e)}')
                return redirect('admin_product_list')
        
        return render(request, 'store/admin/adminproducts.html', {
            'adminproducts': self.get_all_products(),
            'MEDIA_URL': settings.MEDIA_URL
        })

    def post(self, request, product_id):
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied

        try:
            product_uri = URIRef(self.ECOM_NS + product_id)
            
            if (product_uri, RDF.type, self.ECOM_NS.Product) not in self.graph:
                messages.error(request, 'Product not found')
                return redirect('admin_product_list')

            action = request.POST.get('action')

            if action == 'delete':
                # Get current image path before deleting
                current_image = str(self.graph.value(product_uri, self.ECOM_NS.hasImage))
                if current_image and current_image != "default_image.jpg":
                    # Delete the image file if it exists
                    image_path = os.path.join(settings.MEDIA_ROOT, current_image)
                    if os.path.exists(image_path):
                        os.remove(image_path)

                # Remove all triples about this product
                for p, o in self.graph.predicate_objects(product_uri):
                    self.graph.remove((product_uri, p, o))
                messages.success(request, 'Product deleted successfully')
                
            elif action == 'update':
                # Update basic product information
                updates = {
                    self.ECOM_NS.price: Literal(float(request.POST.get('price', 0)), 
                                              datatype=XSD.float),
                    self.ECOM_NS.stockLevel: Literal(int(request.POST.get('stock_level', 0)), 
                                                   datatype=XSD.integer),
                    self.ECOM_NS.discount: Literal(float(request.POST.get('discount', 0)), 
                                                 datatype=XSD.float)
                }

                # Handle image update
                if request.FILES.get('image'):
                    # Delete old image if it exists and isn't the default
                    current_image = str(self.graph.value(product_uri, self.ECOM_NS.hasImage))
                    if current_image and current_image != "default_image.jpg":
                        old_image_path = os.path.join(settings.MEDIA_ROOT, current_image)
                        if os.path.exists(old_image_path):
                            os.remove(old_image_path)

                    # Save new image
                    image = request.FILES['image']
                    image_path = os.path.join('product_images', image.name)
                    full_path = os.path.join(settings.MEDIA_ROOT, image_path)
                    
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(full_path), exist_ok=True)
                    
                    with open(full_path, 'wb+') as f:
                        for chunk in image.chunks():
                            f.write(chunk)
                    
                    updates[self.ECOM_NS.hasImage] = Literal(image_path, datatype=XSD.string)

                # Update the graph
                for predicate, new_value in updates.items():
                    old_value = self.graph.value(product_uri, predicate)
                    if old_value:
                        self.graph.remove((product_uri, predicate, old_value))
                    self.graph.add((product_uri, predicate, new_value))

                messages.success(request, 'Product updated successfully')

            self.save_graph()
            return redirect('admin_product_list')
            
        except Exception as e:
            messages.error(request, f'Error processing product: {str(e)}')
            return redirect('admin_product_list')

class OrderView(LoginRequiredMixin, BaseOntologyView):
    """Handle order creation and management"""
    def get(self, request):
        if request.session.get('user_type') != 'user':
            raise PermissionDenied
            
        # Get both promotional and regular products
        user_product_view = UserProductView()
        promotional_products, regular_products = user_product_view.get_products_by_discount()
        
        # Combine all products for the order form
        all_products = promotional_products + regular_products
        
        return render(request, 'store/user/order_form.html', {
            'products': all_products,
            'MEDIA_URL': settings.MEDIA_URL
        })
    
    def post(self, request):
        try:
            product_name = request.POST.get('product_name')
            quantity = int(request.POST.get('quantity', 0))
            
            # Validate input
            if quantity <= 0:
                messages.error(request, 'Quantity must be greater than 0')
                return redirect('order')
            
            # Find product
            product = next(
                (p for p in self.graph.subjects(self.ECOM_NS.name, 
                Literal(product_name, datatype=XSD.string))), None
            )
            
            if not product:
                messages.error(request, 'Product not found')
                return redirect('order')
            
            # Check stock
            stock = int(self.graph.value(product, self.ECOM_NS.stockLevel))
            if stock < quantity:
                messages.error(request, f'Insufficient stock. Only {stock} available')
                return redirect('order')
            
            # Get product price and discount
            price = float(self.graph.value(product, self.ECOM_NS.price))
            discount = float(self.graph.value(product, self.ECOM_NS.discount, 
                                            default=Literal(0.0)))
            
            # Calculate final price
            final_price = price * (1 - discount/100)
            
            # Create order
            order_id = str(uuid.uuid4())
            order = URIRef(self.ECOM_NS + order_id)
            
            # Add order details
            order_data = [
                (RDF.type, self.ECOM_NS.Order),
                (self.ECOM_NS.customer, Literal(request.session.get('username', 'Unknown'), 
                                              datatype=XSD.string)),
                (self.ECOM_NS.product, product),
                (self.ECOM_NS.quantity, Literal(quantity, datatype=XSD.integer)),
                (self.ECOM_NS.price, Literal(final_price, datatype=XSD.float)),
                (self.ECOM_NS.status, Literal("pending", datatype=XSD.string)),
                (self.ECOM_NS.orderDate, Literal(datetime.now().isoformat(), 
                                               datatype=XSD.dateTime))
            ]
            
            for predicate, obj in order_data:
                self.graph.add((order, predicate, obj))
            
            # Update stock
            self.graph.set((product, self.ECOM_NS.stockLevel, Literal(stock - quantity)))
            
            self.save_graph()
            messages.success(request, 'Order placed successfully!')
            return redirect('order_success')
            
        except Exception as e:
            messages.error(request, f'Error processing order: {str(e)}')
            return redirect('order')

class ViewOrdersView(LoginRequiredMixin, BaseOntologyView):
    """View and manage orders"""
    def get(self, request):
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied
            
        orders = []
        for order in self.graph.subjects(RDF.type, self.ECOM_NS.Order):
            try:
                order_data = {
                    'id': str(order).split('#')[-1],
                    'customer': str(self.graph.value(order, self.ECOM_NS.customer) or "Unknown"),
                    'product': str(self.graph.value(self.graph.value(order, self.ECOM_NS.product), 
                                                  self.ECOM_NS.name) or "Unknown Product"),
                    'quantity': int(self.graph.value(order, self.ECOM_NS.quantity) or 0),
                    'status': str(self.graph.value(order, self.ECOM_NS.status) or "unknown"),
                    'date': self.graph.value(order, self.ECOM_NS.orderDate)
                }
                orders.append(order_data)
            except Exception as e:
                print(f"Error processing order {order}: {str(e)}")
                continue
            
        return render(request, 'store/admin/orders.html', {'orders': orders})

class AdminView(LoginRequiredMixin, BaseOntologyView):
    """Admin dashboard and product management"""
    def get(self, request):
        if request.session.get('user_type') != 'admin':
            raise PermissionDenied
            
        products = AdminProductView().get_all_products()
        return render(request, 'store/admin/dashboard.html', {'products': products})

    def post(self, request):
        try:
            # Validate input
            product_name = request.POST.get('name')
            if not product_name:
                messages.error(request, 'Product name is required')
                return redirect('baseAdmin')
                
            # Process product data
            product_data = {
                'price': float(request.POST.get('price', 0)),
                'stock_level': int(request.POST.get('stock_level', 0)),
                'discount': float(request.POST.get('discount', 0)),
            }
            
            # Handle image upload
            image = request.FILES.get('image')
            if image:
                image_path = os.path.join('product_images', image.name)
                os.makedirs(os.path.dirname(os.path.join(settings.MEDIA_ROOT, image_path)), 
                           exist_ok=True)
                with open(os.path.join(settings.MEDIA_ROOT, image_path), 'wb+') as f:
                    for chunk in image.chunks():
                        f.write(chunk)
            else:
                image_path = 'default_image.jpg'

            # Create product in RDF graph
            product_id = product_name.lower().replace(" ", "_")
            product = URIRef(self.ECOM_NS + product_id)

            # Add product properties
            product_properties = [
                (RDF.type, self.ECOM_NS.Product),
                (self.ECOM_NS.name, Literal(product_name, datatype=XSD.string)),
                (self.ECOM_NS.price, Literal(product_data['price'], datatype=XSD.float)),
                (self.ECOM_NS.stockLevel, Literal(product_data['stock_level'], 
                                                datatype=XSD.integer)),
                (self.ECOM_NS.discount, Literal(product_data['discount'], datatype=XSD.float)),
                (self.ECOM_NS.hasImage, Literal(image_path, datatype=XSD.string))
            ]

            for predicate, obj in product_properties:
                self.graph.add((product, predicate, obj))

            self.save_graph()
            messages.success(request, 'Product added successfully!')
            return redirect('baseAdmin')
            
        except Exception as e:
            messages.error(request, f'Error adding product: {str(e)}')
            return redirect('baseAdmin')
